from ganglion import OpenBCIGanglion

def print_raw(data):
    print(data.channels_data)

board = OpenBCIGanglion(mac='e6:5d:54:f2:f4:38')
board.write_command('[')
board.start_stream(print_raw)
