import socket

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
result = sock.connect_ex(('10.9.1.75', 12345))
if result == 0:
    print("Port is open")
else:
    print("Port is closed or unreachable")
sock.close()