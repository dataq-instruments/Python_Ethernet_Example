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
        #for chart in self.strip_charts :
        #    print("Found DataQ DAQ in config %s expected on IP %s" % (chart[0], chart[1]['ip_address']))

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
        print("Sending UDP Broadcast '%s' " % (msg.decode()))
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
    hardware_info = {'Strip_Chart_1': {'ip_address': '192.168.0.80',}}

    # This specifies which channels to setup and how to set them up
    stripcharts_info = {'Example_Channel_Name_01': {'channel': 6, 'strip_chart': 'Strip_Chart_2', 
                                                    'daq_scale': 1, 'value_scale': 1000}}

    # Initialize the class
    #print ("stripcharts_info=", stripcharts_info)
    dataq = DataQDI4370Ethernet(hardware_dict=hardware_info,
                                 stripchart_setup_dict=stripcharts_info)


    # IF ADC is running, attempt to stop here, wait for disconnect to complete
    time.sleep(2)

    dataq.read_messages()  # Clear out any messages
    dataq.do_udp_discovery()




