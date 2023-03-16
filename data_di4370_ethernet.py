import socket
import re
import time
import signal
from pathlib import Path
import argparse
import struct
import random
import sys
from ping3 import ping
import datetime
import numpy as np
import keyboard


class DataQDI4370Ethernet:
    # *** UDP Port Number Function *** 
    # 1235 (fixed)         Device's discovery receiving port
    # 1234 (programmable)  PC's default discovery receiving port.
    # 51235 (fixed)        Device's command receiving port
    # 1234 (programmable)  PC's default status/data receiving port. Programmable via the PORT command.

    def __init__(self, hardware_dict=None, stripchart_setup_dict=None, ip_address='0.0.0.0'):
        self.ip_address = ip_address
        self.socket_buffer_size = 2048

        self.hardware_dict = hardware_dict
        self.stripchart_setup_dict = stripchart_setup_dict

        # Parse our hardware yaml file and see which ones we're interested in
        self.strip_charts = [[hardware,hardware_dict[hardware]] for hardware in hardware_dict]
        for chart in self.strip_charts :
            print("Found DataQ DAQ in config %s expected on IP %s" % (chart[0], chart[1]['ip_address']))

        self.new_group_id = random.randint(1,9)  # Spec recommends setting this randomly

        # Open socket for sending broadcast and another to receive our responses
        self.disc_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)  # UDP
        self.disc_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        hostname=socket.gethostname()
        IPAddr=socket.gethostbyname(hostname)
        
        print ("PC's IP is ", IPAddr)
        #self.disc_sock.bind(('',1235))       # DataQ device's discovery receiving port, from documentation
        self.disc_sock.bind((IPAddr,1235))       # DataQ device's discovery receiving port, from documentation
        print ("Done binding!")

        # socket for receiving
        self.rec_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        #self.rec_sock.bind((self.ip_address,1234))  # Have to make sure this port is open --> 'sudo ufw allow 1234/udp'
        self.rec_sock.bind((IPAddr,1234))  # Have to make sure this port is open --> 'sudo ufw allow 1234/udp'

        # Cumulative counts for messages received from units
        self.cumulative_count = {}
        for hardware in hardware_dict:
            self.cumulative_count[hardware_dict[hardware]['ip_address']] = 0

    # GroupID = 0 indicates and idle (available) device
    # The only command the DataQ will respond to when GroupID is 0 is the "connect" command (GroupID = 10)?
    # Create packed command per the Protocol Document, page # 9
    def pack_command(self, groupid=1,command='Connect',arg0=0,arg1=0,arg2=0,payload=''):
        ethernet_commands = {'SyncStart': 1,        # HAVE to use this to 'start' with Ethernet devices
                             'SlaveIp': 5,
                             'SyncStop': 6,
                             'Connect': 10,         # These are 'Ethernet-specific' command values
                             'Disconnect': 11,      
                             'KeepAlive': 12,
                             'SetWdqHeader': 21,    # For shared commands , the command goes in the 'Payload'
                             'Shared': 13,}   # All Shared (both USB and Ethernet) protocol commands have a value of 13
        # '@' means 'native' Byte Order, Size, and Alignment, 'I' is 'unsigned int', 'c' is 'char'
        # 0x31415926 is 'DQCommand' --> This is fixed for all commands to DataQ unit
        # 'DQCommand' is fixed length, so we can use pack directly
        payload = payload + '\0' # A null-terminated ASCII string or binary image as needed by the specific command
        payload = bytes(payload.encode('ascii'))
        pack_format = "@IIIIII%ds"%len(payload)
        packed = struct.pack(pack_format,0x31415926,groupid,ethernet_commands[command],arg0,arg1,arg2,payload)
        return packed

    # IF ADC is running, attempt to stop here
    def stop_devices(self):
        for id_id in range (1,10):      # This includes 1, but excludes 10, matches our numbers used in init
            msg = self.pack_command(groupid=id_id,command='SyncStop')
            self.disc_sock.sendto(msg, ("255.255.255.255", 51235))     # Device's command receiving port
            msg = self.pack_command(groupid=id_id,command='Disconnect')
            self.disc_sock.sendto(msg, ("255.255.255.255", 51235))     # Device's command receiving port

    # Reads messages from unit based on response type and decoces into a list of messages
    def read_messages(self, print_data=False, data_type="DQResponse", timeout=3, expected_count=None,decode=True):
        self.rec_sock.settimeout(timeout)          # Set timeout, will break out of our try below

        # Read messages here
        messages = []
        while True:
            try:
                data, addr = self.rec_sock.recvfrom(self.socket_buffer_size)
                messages.append([addr,data])
                if expected_count:
                    if len(messages) >= expected_count:
                        break 
            except:
                break   # Tiemout has occurred

        # Decode messages here
        if decode:
            decoded_messages = []
            for message in messages:

                decoded_message = {}
                decoded_message['IPAddress'] = message[0][0]
                decoded_message['Port'] = message[0][1]

                if data_type == "DQAdcData":

                    unpacked = struct.unpack_from("@IIIIIs", message[1])
                    if unpacked[0] != 0x14142135:
                        raise Exception("Response TYPE does not match expected for DQAdcData!")
                        #print("Response TYPE does not match expected for DQAdcData!")
                        #return None

                    decoded_message['GroupID'] = unpacked[1]
                    decoded_message['Order'] = unpacked[2]
                    decoded_message['CumulativeCount'] = unpacked[3]
                    decoded_message['PayLoadSamples'] = unpacked[4]

                    # Get PayloadSamples from end of our structure
                    PayLoadSamples = [x for ind, x in enumerate(message[1]) if ind >= 20]

                    # Have to use Cumulative Count to stay synchronized here
                    if self.cumulative_count[decoded_message["IPAddress"]] != decoded_message['CumulativeCount']:
                        # raise Exception("Error in cumulative count! Exiting!")
                        print("Error in cumulative count! Resyncronizing!")
                        self.cumulative_count[decoded_message["IPAddress"]] = decoded_message['CumulativeCount']

                    # Each sample is two bytes
                    payload = []
                    for i in range(0, len(PayLoadSamples), 2):
                        lower_byte = PayLoadSamples[i]
                        upper_byte = PayLoadSamples[i+1]
                        upper_byte_shift = upper_byte << 8
                        big_boy_byte = lower_byte + upper_byte_shift
                        payload.append(big_boy_byte)

                    if len(payload) != decoded_message['PayLoadSamples']:
                        raise Exception("Decoded char length does not match expected PayLoadSamples!") 

                    # Add the bytes we receive to our cumulative count
                    self.cumulative_count[decoded_message["IPAddress"]] = \
                    self.cumulative_count[decoded_message["IPAddress"]] + len(payload)

                    # Put our payload list into our thing
                    decoded_message['PayLoadSamples'] = payload

                    # All instruments transmit a 16-bit binary number for every analog channel conversion in 
                    #  the form of a signed, 16-bit Two's complement value

                    # Get twos complement value from bytes
                    def twos(val, bytes=2):
                        b = val.to_bytes(bytes, byteorder=sys.byteorder, signed=False)
                        return int.from_bytes(b, byteorder=sys.byteorder, signed=True)

                    # Get device name to use below
                    device_name = [item for item in self.hardware_dict if \
                    self.hardware_dict[item]['ip_address'] == decoded_message["IPAddress"]][0]

                    # Decoded our PayloadSamples message and create list of sequences
                    sequence = []
                    i = -1
                    for reading in decoded_message['PayLoadSamples']:
                        i = i + 1
                        ch = i % 8

                        daq_conv_scale = self.scales[str(decoded_message["IPAddress"])]['daq_scale'][str(ch)]
                        daq_valu_scale = self.scales[str(decoded_message["IPAddress"])]['value_scale'][str(ch)]
                        conv_reading = (daq_conv_scale * float(twos(reading) / 32768)) * daq_valu_scale

                        channel_name = [item for item in self.stripchart_setup_dict if \
                        self.stripchart_setup_dict[item]['channel'] == ch and \
                        self.stripchart_setup_dict[item]['strip_chart'] == device_name][0]

                        line = "%s value=%f" % (channel_name,conv_reading)
                        sequence.append(line)

                        if print_data:
                            print("Device %s, Reading %03d, Channel %s: %0.2f" % \
                                 (str(decoded_message["IPAddress"]),i, ch,conv_reading))

                    decoded_messages = sequence

                elif data_type == "DQResponse":
                    # data: b'\x18(q!\x05\x00\x00\x00\x00\x00\x00\x00\x0c\x00\x00\x00srate 1000\r\x00'

                    unpacked = struct.unpack_from("@IIIIs", message[1])
                    # print(unpacked)
                    if unpacked[0] != 0x21712818:
                        raise Exception("Response TYPE does not match expected for DQResponse!") 
                    decoded_message['GroupID'] = unpacked[1]
                    decoded_message['Order'] = unpacked[2]
                    decoded_message['PayLoadLength'] = unpacked[3]
                    payload_char = [x for ind, x in enumerate(message[1]) if ind >= 16]
                    if len(payload_char) != decoded_message['PayLoadLength']:
                        raise Exception("Decoded char length does not match expected PayLoadLength!") 
                    payload = "".join(map(chr,payload_char))
                    decoded_message['PayLoad'] = payload.rstrip('\x00').rstrip('\n').rstrip('\r')
                    decoded_messages.append(decoded_message)

            return decoded_messages

        else:
            return messages


    # Do a UDP broadcast to our local network to see what networked DataQ devices we have
    def do_udp_discovery(self):
        msg = b'dataq_instruments'
        print("Sending UDP Broadcast '%s' on IP %s" % (msg.decode(), self.ip_address))
        self.disc_sock.sendto(msg, ("255.255.255.255", 1235))     # Device's discovery receiving port

        # This may be a good candidate for python multiprocessing for receiving UDP on a socket in the future
        messages = []
        while True:
            self.rec_sock.settimeout(3)          # Set timeout to 0.5 second, will break out of our try below
            try:
                data, addr = self.rec_sock.recvfrom(self.socket_buffer_size)
                messages.append([addr,data])
            except:
                break

        # Go through the responses we received in response to our broadcast and parse
        decoded_messages = []
        self.connected_count = 0

        print (messages)

        for message in messages:
            data = message[1].decode()

            # https://www.dataq.com/resources/pdfs/misc/Dataq-Instruments-Protocol.pdf, page 12
            re_string = "(\d{1,3}.\d{1,3}.\d{1,3}.\d{1,3}) " + \
                        "(\w{2}:\w{2}:\w{2}:\w{2}:\w{2}:\w{2}) " + \
                        "(\w*) (\w*) (\w*) (\w*) (\w*) (\w*) (\w*) (\w*) (\w*) (\w*)"
            result = re.search(re_string, data)
            message_contents = ['IP', 'MAC', 'SoftwareRev', 'DeviceModel', 'ADCRunning', 'Reserved', 
                                'LengthOfDescription', 'Description', 'SerialNumber', 'GroupID', 'OrderInGroup', 'Master/Slave']

            decoded_message = {}
            i = 0
            for content in message_contents:
                i = i + 1
                decoded_message[content] = result.group(i)

            decoded_messages.append(decoded_message)

            self.connected_count = self.connected_count + 1

            print("Found DataQ device %s on IP %s" % (decoded_message['DeviceModel'], decoded_message['IP']))
            for message in decoded_message:
                print("   " + message + ": " + decoded_message[message])

        # Verify we find all expected DataQ devices on the local network
        all_found = True
        for chart in self.strip_charts:
            ip_found = False
            for message in decoded_messages:
                if chart[1]['ip_address'] == message['IP']:
                    ip_found = True
            if not ip_found:
                all_found = False

    # Connect all DataQ devices to this computer
    def connect_devices(self):
        # broadcast new GroupID and connect command to all devices
        msg = self.pack_command(groupid=self.new_group_id,command='Connect',payload=self.ip_address)
        self.disc_sock.sendto(msg, ("255.255.255.255", 51235))     # Device's command receiving port

        for message in self.read_messages(expected_count=2):
            if message['GroupID'] == self.new_group_id and message['PayLoad'] == 'connected':
                print("DataQ device on %s has been set to group %s: %s" % \
                      (message['IPAddress'],message['GroupID'],message['PayLoad']))
            else:
                raise Exception("DataQ unit on %s has not connected as expected" % message['IPAddress'])
                # Unit will not connect if it is connected to some other unit
        time.sleep(2)

        # If any enabled device is not issued a KeepAlive command for more than 8 seconds it will drop its session 
        # and GroupID, and fall back to an idle state. This command does not generate a response. 
        # Zero will KeepAlive indefinitely.

        # Ok so this works but the "ethernet-specific" command does not
        msg = self.pack_command(groupid=self.new_group_id,command='Shared',payload="keepalive 0")
        self.disc_sock.sendto(msg, ("255.255.255.255", 51235))     # Device's command receiving port
        got = self.read_messages(expected_count=2)
        # print(str(got))
        if not got:
            msg = "Connect to DATAQ units has failed. If the units have previously been connected " + \
                  "to another computer, then they need to be power cycled before attempting to connect again."
            raise Exception(msg)
        for message in got:
            print(str(message))

    # Basic command for getting info from units
    def get_info(self):
        # Send a basic Info command and verify it works
        msg = self.pack_command(groupid=self.new_group_id,command='Shared',payload="info 1")
        self.disc_sock.sendto(msg, ("255.255.255.255", 51235))     # Device's command receiving port
        for message in self.read_messages(expected_count=2):
            print(str(message))

    # Sets and verifies time on units
    def set_time(self):
        # Set time on units to current UTC time
        current_utc_time = datetime.datetime.now(datetime.timezone.utc)
        ymd_string = "ymd %04d/%02d/%02d" % (current_utc_time.year,current_utc_time.month,current_utc_time.day)
        hms_string = "hms %02d:%02d:%02d" % (current_utc_time.hour,current_utc_time.minute,current_utc_time.second)

        msg = self.pack_command(groupid=self.new_group_id,command='Shared',payload=hms_string)
        self.disc_sock.sendto(msg, ("255.255.255.255", 51235))     # Device's command receiving port
        for message in self.read_messages(expected_count=2):
            print(str(message))

        msg = self.pack_command(groupid=self.new_group_id,command='Shared',payload=ymd_string)
        self.disc_sock.sendto(msg, ("255.255.255.255", 51235))     # Device's command receiving port
        for message in self.read_messages(expected_count=2):
            print(str(message))

        for message in self.read_messages():
            print(str(message))
            ymd_parse = "ymd (\d{4})\/(\d{2})\/(\d{2})"
            ymd_parsed = re.search(ymd_parse, message['PayLoad'])

            print(str(message))
            hms_parse = "hms (\d{2}):(\d{2}):(\d{2})"
            hms_parsed = re.search(hms_parse, message['PayLoad'])

        unit_time = datetime.datetime(year=int(ymd_parsed.group(1)),
                                      month=int(ymd_parsed.group(2)),
                                      day=int(ymd_parsed.group(3)),
                                      hour=int(hms_parsed.group(1)),
                                      minute=int(hms_parsed.group(2)),
                                      second=int(hms_parsed.group(3)),)
        print(str(current_utc_time))
        print(str(unit_time))       # This should be UTC
        print(str(current_utc_time.timestamp()))
        print(str(unit_time.timestamp()))       # This should be UTC

    # Set EOL for ASCII if used
    def set_ascii_eol(self):
        # Set EOL character if we use ASCII mode
        msg = self.pack_command(groupid=self.new_group_id,command=ethernet_commands['Shared'],payload="eol 1")
        self.disc_sock.sendto(msg, ("255.255.255.255", 51235))     # Device's command receiving port
        for message in self.read_messages():
            print(str(message))

    # Sequence for doing bulk of setup for units
    def send_setup_commands(self, dec=1, deca=1, sample_rate=1000, packet_size=16, encoding='binary'):
        # Hertz, how often to sample each channel (Samples/second/channel - 'S/s/channel')
        self.sample_rate = sample_rate
        self.packet_size = packet_size

        # Map packet size to commandable parameter for Data-Q units
        dataq_packet_dict = {16: 0,     # ps 0 - Make packet size 16 bytes (DEFAULT)
                             32: 1,     # ps 1 - Make packet size 32 bytes
                             64: 2,     # ps 2 - Make packet size 64 bytes
                             128: 3,    # ps 3 - Make packet size 128 bytes
                             256: 4,    # ps 4 - Make packet size 256 bytes
                             512: 5,    # ps 5 - Make packet size 512 bytes
                             1024: 6,   # ps 6 - Make packet size 1024 bytes
                             2048: 7}   # ps 7 - Make packet size 2048 bytes


        # Set the encoded to 0 (binary), could also be 1 (for ASCII)
        if 'ascii' in encoding:
            msg = self.pack_command(groupid=self.new_group_id,command='Shared',payload="encode 1")
            self.disc_sock.sendto(msg, ("255.255.255.255", 51235))     # Device's command receiving port
            for message in self.read_messages(expected_count=2):
                print(str(message))
            self.set_ascii_eol()
        else:
            msg = self.pack_command(groupid=self.new_group_id,command='Shared',payload="encode 0")
            self.disc_sock.sendto(msg, ("255.255.255.255", 51235))     # Device's command receiving port
            for message in self.read_messages(expected_count=2):
                print(str(message))

        # Set rate stuff
        msg = self.pack_command(groupid=self.new_group_id,command='Shared',payload="dec %s" % dec)
        self.disc_sock.sendto(msg, ("255.255.255.255", 51235))     # Device's command receiving port
        for message in self.read_messages(expected_count=2, timeout=5):
            print(str(message))

        msg = self.pack_command(groupid=self.new_group_id,command='Shared',payload="deca %s" % deca)
        self.disc_sock.sendto(msg, ("255.255.255.255", 51235))     # Device's command receiving port
        for message in self.read_messages(expected_count=2, timeout=5):
            print(str(message))

        self.read_messages()  # Clear out any messages

        # Command srate defines the value of a sample rate divisor used to determine scan rate
        srate = int(60000000 / self.sample_rate / dec / deca)
        print("Computed srate: %s" % srate)

        if srate > 65535:
            msg = "Srate is too large for DI-4730 unit!"
            raise Exception(msg)
        elif srate < 375:
            msg = "Srate is too small for DI-4730 unit!"
            raise Exception(msg) 

        # Set calculated srate
        msg = self.pack_command(groupid=self.new_group_id,command='Shared',payload="srate %s" % srate)
        self.disc_sock.sendto(msg, ("255.255.255.255", 51235))     # Device's command receiving port
        for message in self.read_messages(expected_count=2):
            print(str(message))

        # Create list of slist commands to send
        self.scales = {}
        slist_config = {}
        for chart in self.strip_charts:
            slists = []
            slist_i = -1

            daq_scale = {}
            conversion_scales = {}
            for ch in range(0,8):
                for info in stripcharts_info:
                    if stripcharts_info[info]['strip_chart'] == chart[0] and stripcharts_info[info]['channel'] == ch:
                        slist_i = slist_i + 1

                        daq_scale[str(ch)] = stripcharts_info[info]['daq_scale']
                        conversion_scales[str(ch)] = stripcharts_info[info]['value_scale']
                        scale = stripcharts_info[info]['daq_scale']

                        if scale == 1000:
                            range_table = 0b0000
                        elif scale == 100:
                            range_table = 0b0001
                        elif scale == 10:
                            range_table = 0b0010
                        elif scale == 1:
                            range_table = 0b0011
                        elif scale == 0.1:
                            range_table = 0b0100

                        scan_list_definition = ch + (range_table << 8)
                        slist = "slist %s %s" % (slist_i, scan_list_definition)
                        slists.append(slist)

            self.scales[self.hardware_dict[chart[0]]['ip_address']] = {'daq_scale': daq_scale,
                                'value_scale': conversion_scales,}
            slist_config[str(chart[0])] = slists

        # Send slist commands to units
        for chart in self.strip_charts:
            if ping(chart[1]['ip_address']):
                for slist in slist_config[chart[0]]:
                    msg = self.pack_command(groupid=self.new_group_id,command='Shared',payload=slist)

                    print("sending command: %s" % slist)
                    self.disc_sock.sendto(msg, (chart[1]['ip_address'], 51235))     # Device's command receiving port

                    for message in self.read_messages(expected_count=1):
                        print(str(message))

        # Set packet size
        msg = self.pack_command(groupid=self.new_group_id,command='Shared', \
                                payload="ps %s" % dataq_packet_dict[packet_size])
        self.disc_sock.sendto(msg, ("255.255.255.255", 51235))     # Device's command receiving port
        for message in self.read_messages(expected_count=2):
            print(str(message))

    # Send syncstart and keepalive commands to actualy start DAQ running
    def start(self):

        # SyncStart is necessary to start DAQ running on ethernet, also Keep Alive
        msg = self.pack_command(groupid=self.new_group_id,command='SyncStart')
        self.disc_sock.sendto(msg, ("255.255.255.255", 51235))     # Device's command receiving port

        msg = self.pack_command(groupid=self.new_group_id,command='KeepAlive')
        self.disc_sock.sendto(msg, ("255.255.255.255", 51235))     # Device's command receiving port



# Demonstration of how to use this class if it is run as main
if __name__ == "__main__":
    import logging
    from logging.handlers import TimedRotatingFileHandler

    # Add command line arguments
    ap = argparse.ArgumentParser()
    ap.add_argument("-log", "--log-name", required=False, default='example_log/example.log', type=str, 
                    help="log file to log to")
    args = vars(ap.parse_args())

    # This maps the IP address of the units to a name used below to configure individual channels
    hardware_info = {'Strip_Chart_1': {'ip_address': '192.168.0.80',}, 
                     'Strip_Chart_2': {'ip_address': '192.168.0.81',},}

    # This specifies which channels to setup and how to set them up
    stripcharts_info = {'Example_Channel_Name_01': {'channel': 6, 'strip_chart': 'Strip_Chart_2', 
                                                    'daq_scale': 1, 'value_scale': 1000}, 
                        'Example_Channel_Name_02': {'channel': 7, 'strip_chart': 'Strip_Chart_2', 
                                                    'daq_scale': 1000, 'value_scale': 1}, 
                        'Example_Channel_Name_03': {'channel': 5, 'strip_chart': 'Strip_Chart_1', 
                                                    'daq_scale': 1, 'value_scale': 1000}, 
                        'Example_Channel_Name_04': {'channel': 4, 'strip_chart': 'Strip_Chart_1', 
                                                    'daq_scale': 1000, 'value_scale': 1}, 
                        'Example_Channel_Name_05': {'channel': 7, 'strip_chart': 'Strip_Chart_1', 
                                                    'daq_scale': 1, 'value_scale': 1000}, 
                        'Example_Channel_Name_06': {'channel': 6, 'strip_chart': 'Strip_Chart_1', 
                                                    'daq_scale': 1000, 'value_scale': 1}, 
                        'Example_Channel_Name_07': {'channel': 1, 'strip_chart': 'Strip_Chart_2', 
                                                    'daq_scale': 0.1, 'value_scale': 1000}, 
                        'Example_Channel_Name_08': {'channel': 0, 'strip_chart': 'Strip_Chart_2', 
                                                    'daq_scale': 1000, 'value_scale': 1}, 
                        'Example_Channel_Name_09': {'channel': 3, 'strip_chart': 'Strip_Chart_2', 
                                                    'daq_scale': 1, 'value_scale': 1000}, 
                        'Example_Channel_Name_10': {'channel': 2, 'strip_chart': 'Strip_Chart_2', 
                                                    'daq_scale': 1000, 'value_scale': 1}, 
                        'Example_Channel_Name_11': {'channel': 4, 'strip_chart': 'Strip_Chart_2', 
                                                    'daq_scale': 1, 'value_scale': 1000}, 
                        'Example_Channel_Name_12': {'channel': 5, 'strip_chart': 'Strip_Chart_2', 
                                                    'daq_scale': 100, 'value_scale': 1}, 
                        'Example_Channel_Name_13': {'channel': 1, 'strip_chart': 'Strip_Chart_1', 
                                                    'daq_scale': 0.1, 'value_scale': 10000}, 
                        'Example_Channel_Name_14': {'channel': 0, 'strip_chart': 'Strip_Chart_1', 
                                                    'daq_scale': 100, 'value_scale': 1}, 
                        'Example_Channel_Name_15': {'channel': 3, 'strip_chart': 'Strip_Chart_1', 
                                                    'daq_scale': 1, 'value_scale': 1000}, 
                        'Example_Channel_Name_16': {'channel': 2, 'strip_chart': 'Strip_Chart_1', 
                                                    'daq_scale': 100, 'value_scale': 1}}

    # Initialize the class
    #print ("stripcharts_info=", stripcharts_info)
    dataq = DataQDI4370Ethernet(hardware_dict=hardware_info,
                                 stripchart_setup_dict=stripcharts_info)

    # Add an event handler to run stop_devices function if script is killed
    def handler(signum, frame):
        try:
            dataq.stop_devices()
        except:
            pass     # Want to continue to exit if this logic fails
        exit(1)
    
    # Setup a handler for all signals
    for i in [x for x in dir(signal) if x.startswith("SIG")]:
      try:
        signum = getattr(signal,i)
        signal.signal(signum,handler)
      except:
        pass

    def create_timed_rotating_log(path):
        """"""
        logger = logging.getLogger("Rotating Log")
        logger.setLevel(logging.INFO)
        
        # Will log to a rotating file for 1 minute, then pinch off up to 5 logs
        handler = logging.handlers.TimedRotatingFileHandler(path, when="m", interval=1, backupCount=5)
        logger.addHandler(handler)
        return logger
        
    log = create_timed_rotating_log(args['log_name'])

    # Writes to log, creates appropriate nanosecond timestamp
    def write_to_log(sequence, duration, logger):
        stop_time = time.time_ns()
        start_time = time.time_ns()-(duration * (10**9))

        time_sequence = np.linspace(start_time, stop_time, len(sequence))
        time_sequence = time_sequence.astype(np.int64)
        time_sequence = time_sequence.tolist()

        send_sequence = []
        for i in range(0,len(sequence)):
            send_sequence.append(sequence[i] + " time_ns=" + str(time_sequence[i]))

        logger.info("\n".join(str(item) for item in send_sequence))

    # IF ADC is running, attempt to stop here, wait for disconnect to complete
    dataq.stop_devices()
    time.sleep(2)

    dataq.read_messages()  # Clear out any messages
    dataq.do_udp_discovery()
    dataq.connect_devices()
    dataq.send_setup_commands()
    dataq.start()

    print("Getting data... (press X to quit)")

    # Just read back any information received 
    init_time = time.time()
    sequences = []
    while(True):
        try:
            if keyboard.is_pressed('x' or 'X'):
                dataq.stop_devices()
                print("Bye!")
                break
            else:    
                sequences = sequences + dataq.read_messages(print_data=False, data_type='DQAdcData', \
                                    timeout=30,decode=True, expected_count=1)
        except:
            pass

        if len(sequences) > dataq.sample_rate * 8:
            duration = time.time() - init_time
            # print(duration)
            # print("%0.2f S/s/Channel" % (len(sequences) / duration / dataq.connected_count / 8))
            init_time = time.time()

            write_to_log(sequences, duration, logger=log)
            sequences = []
