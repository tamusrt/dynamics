import time
import socket
import sys

# ------------------------------------------------------------------------------
# Establishes TCP connection with the Weather Raspberry Pi TCP Server to collect
# data packets from the ambient weather weather station. Packets are then
# written to a text file for plotting
# Written: Sarah Kinney SRT9
# ------------------------------------------------------------------------------

weather_fname = 'dev_weather_hearne.txt'

data_dict = dict(time=[0], temperature_C=[0], humidity=[0], pressure_hPa=[0])

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
TCP_IP = '192.168.1.5'
TCP_PORT = 4040
server_address = (TCP_IP, TCP_PORT)

def testConnection():
    try:
        sock.connect(server_address)
        print("connected")
        return True
    except TimeoutError:
        print("no connection")
        return False
    except OSError:
        print("no connection")
        return False

print(testConnection())


while True:
    try:
        with open(weather_fname, 'ab') as raw_log:
            data = sock.recv(282)
            print("received %s" % data)
            raw_log.write(data)
    except:
        testConnection()

    time.sleep(16)
    
with open('dev_weather_raw.txt', 'ab')as raw_log:
    raw_log.write('\n')
    


    
    

            
