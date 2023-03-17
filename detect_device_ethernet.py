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



# Demonstration of how to use this class if it is run as main
if __name__ == "__main__":
    import logging
    from logging.handlers import TimedRotatingFileHandler

    dataq = DataQDI4370Ethernet()

    dataq.do_udp_discovery()




