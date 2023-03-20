# Python_Ethernet_Example
An Ethernet python example contributed by our customer. It was originally designed to work with DI-4730 but with minor modification, it can work with 4108, 4208 and 4718B Ethernet

1) Searches for all DATAQ ethernet devices on the network
2) Connect to the IP address(es) listed in hardware_info of main
3) Start collecting data and save to the log file defined in ap.add_argument of main, which is default to "example_log/example.log"
4) Press 'x' on your keyboard to exit the program

detect_device_ethernet.py only perform 1). It is a simplied version of data_di4370_ethernet.py for easy reading.

data_di4370_ethernet.py will do all above

