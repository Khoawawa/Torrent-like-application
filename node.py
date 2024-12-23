# built-in libraries
from utils import *
import os
import sys
import argparse
from threading import Thread, Timer, Lock
import threading
from operator import itemgetter
import datetime
import time
from itertools import groupby
import mmap
import errno
import warnings
import struct
warnings.filterwarnings("ignore")

# implemented classes
from configs import CFG, Config
config = Config.from_json(CFG)
from messages.message import Message
from messages.node2tracker import Node2Tracker
from messages.node2node import Node2Node, NodeInfo
from messages.chunk_sharing import ChunkSharing
from segment import UDPSegment
from torrent_file import TorrentFile
next_call = time.time()

class Node:
    def __init__(self, node_id: int):
        #id of node
        self.node_id = node_id
        # listening socket of node
        
        self.rcv_socket = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.rcv_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.rcv_socket.bind((socket.gethostbyname(socket.gethostname()),0))
        self.rcv_socket.listen(5)

        self.files = self.fetch_owned_files()
        self.is_in_send_mode = False    # is thread uploading a file or not
        self.downloaded_files = {}
        self.torrent_data = None
        print(self.rcv_socket.getsockname())

    def split_file_to_chunks(self, file_path: str, rng: tuple) -> list:
        
        with open(file_path, "r+b") as f:
            mm = mmap.mmap(f.fileno(), 0)[rng[0]: rng[1]]
            # we divide each chunk to a fixed-size pieces to be transferable
            piece_size = config.constants.CHUNK_PIECES_SIZE
            return [mm[p: p + piece_size] for p in range(0, rng[1] - rng[0], piece_size)]

    def reassemble_file(self, chunks: list, file_path: str):
        with open(file_path, "bw+") as f:
            for ch in chunks:
                f.write(ch)
            f.flush()
            f.close()
    def split_files_to_chunk(self,folder_path: str,rng: tuple):
        files = [f for f in os.listdir(folder_path)]
        initial_byte,ending_byte = rng
        piece_size = config.constants.CHUNK_PIECES_SIZE
        chunk_pieces = []
        folder_offset = 0
        for file in files:
            file_path = os.path.join(folder_path,file)
            file_size = os.path.getsize(file_path)
            
            if initial_byte >= file_size + folder_offset:
                folder_offset += file_size
                continue
            with open(file_path, "r+b") as f:
                mm = mmap.mmap(f.fileno(), 0)
                
                start = max(0, initial_byte - folder_offset)
                end = min(file_size,ending_byte - folder_offset)
                
                for p in range(start,end,piece_size):
                    chunk_pieces.append(mm[p: min(p+piece_size, end)])
            folder_offset += file_size
            
            if folder_offset >= ending_byte:
                break
        return chunk_pieces
    
    def send_chunk(self, info_hash, rng: tuple, dest_node_id: int, dest_addr: tuple,conn_socket):
        
        file_name = self.torrent_data['info']['file_name']
        file_path = f"{config.directory.node_files_dir}node{self.node_id}/{file_name}"
        if 'files' in self.torrent_data['info']:
            chunk_pieces = self.split_files_to_chunk(folder_path=file_path,rng=rng)
        else:
            chunk_pieces = self.split_file_to_chunks(file_path=file_path,
                                                 rng=rng)
        temp_sock = conn_socket
        
        for idx, p in enumerate(chunk_pieces):
            msg = ChunkSharing(src_node_id=self.node_id,
                               dest_node_id=dest_node_id,
                               info_hash=info_hash,
                               range=rng,
                               idx=idx,
                               chunk=p)

            encoded_msg = msg.encode()
            msg_length = len(encoded_msg)
            length_prefix = struct.pack("!I", msg_length)
            temp_sock.sendall(length_prefix + encoded_msg)   

            log_content = f"The {idx}/{len(chunk_pieces)} has been sent!"
            log(node_id=self.node_id, content=log_content)
            print(f"Message size: {sys.getsizeof(encoded_msg)} bytes")

            
        # now let's tell the neighboring peer that sending has finished (idx = -1)
        msg = ChunkSharing(src_node_id=self.node_id,
                           dest_node_id=dest_node_id,
                           info_hash=info_hash,
                           range=rng)
        

        encoded_msg = msg.encode()
        # Send the length of the finish message followed by the message itself
        msg_length = len(encoded_msg)
        length_prefix = struct.pack("!I", msg_length)
        temp_sock.sendall(length_prefix + encoded_msg)

        log_content = "The process of sending a chunk to node{} of file {} has finished!".format(dest_node_id, file_name)
        log(node_id=self.node_id, content=log_content)

        msg = Node2Tracker(node_id=self.node_id,
                           mode=config.tracker_requests_mode.UPDATE,
                           info_hash=info_hash,
                           left=0,
                           port=self.rcv_socket.getsockname()[1])
        
        tracker_sock = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        tracker_sock.connect(tuple(config.constants.TRACKER_ADDR))
        tracker_sock.sendall(msg.encode())
        tracker_sock.close()

    def send_info(self, dest_node_id, dest_addr, conn_socket):
        # temp_port = generate_random_port()
        # temp_sock = set_socket(temp_port)
        msg = NodeInfo(self.node_id, dest_node_id, self.torrent_data['info_hash'], self.torrent_data['info'])
        
        temp_sock = conn_socket
        temp_sock.send(msg.encode())
        log_content = f"Node {self.node_id} has sent info to Node {dest_node_id}!"

        log(node_id=self.node_id, content=log_content)
    def handle_requests(self, msg: dict, addr: tuple, conn_socket):
        # 1. asks the node about a file size
        if "size" in msg.keys() and msg["size"] == -1:
            self.tell_file_size(msg=msg, addr=addr)
        # 2. Wants a chunk of a file
        elif "range" in msg.keys() and msg["chunk"] is None:
            self.send_chunk(info_hash=msg["info_hash"],
                            rng=msg["range"],
                            dest_node_id=msg["src_node_id"],
                            dest_addr=addr, conn_socket=conn_socket)
            conn_socket.close()
        elif "info" in msg.keys() and msg["info"] is None:
            self.send_info(dest_node_id=msg["src_node_id"],dest_addr=addr, conn_socket=conn_socket)
            conn_socket.close()

    def listen(self):
        log_content = 'Listening on port {0}'.format(self.rcv_socket.getsockname()[1])
        log(self.node_id,log_content)
        try:
            while True:
                try:
                    conn,addr = self.rcv_socket.accept()
                    log_content = f'Connected to {addr}'
                    log(self.node_id,log_content)
                    data = conn.recv(config.constants.BUFFER_SIZE)
                    msg = Message.decode(data)
                    self.handle_requests(msg=msg, addr=addr, conn_socket = conn)
                except socket.error as e:
                    if e.errno == errno.WSAENOTSOCK:
                        log_content = "Receiving Socket was closed: stopping listener"
                        log(self.node_id, log_content)
                        break
        finally:
            log_content = "Stopped listening to request"
            log(self.node_id,log_content)

    def set_send_mode(self, torrent):
        # if file_name not in self.files:
        #     log(node_id=self.node_id,
        #         content=f"You don't have {file_name}")
        #     return
        # file_path = f'{config.constants.node_files_dirs}node{self.node_id}/{file_name}'
        # try:
        #     file_size = os.path.getsize(file_path)
        # except FileNotFoundError:
        #     log(node_id=self.node_id, content=f"File {file_name} not found at {file_path}")
        #     return
        # except PermissionError:
        #     log(node_id=self.node_id, content=f"Permission denied for file {file_name} at {file_path}")
        #     return
        # except OSError as e:  # Generic error for unexpected issues
        #     log(node_id=self.node_id, content=f"Error accessing file {file_name}: {e}")
        #     return
        
        # torrent = TorrentFile(file_name,file_size,file_path,True)
        # torrent_data = torrent.create_torrent_data()
        # torrent.create_torrent_file(self.node_id,torrent_data)
        # info_hash = torrent_data['info_hash']
        # CHECK IF THE TORRENT EXIST
        torrent_path = config.directory.node_files_dir + f'node{self.node_id}/' + torrent
        if not os.path.exists(torrent_path):
            log_content = f'You don\'t have the torrent file {torrent}' 
            log(self.node_id,log_content)
            return
        
        tracker_url,info_hash,info = TorrentFile.load_torrent_file(config.directory.node_files_dir + f'node{self.node_id}/' + torrent)
       
        # CHECK IF THE FILE EXIST
        path = config.directory.node_files_dir + f'node{self.node_id}/' + info["file_name"]
        if not os.path.exists(path):
            log_content = f'You don\'t have the {"folder" if "files" in info else "file"} {info["file_name"]}'  
            log(self.node_id, log_content)
            return

        if "files" in info:
            for file_info in info["files"]:
                file_path = path + f'/{file_info["file_name"]}'
                if not os.path.exists(file_path):
                    log_content = f'You don\'t have the file {file_info["file_name"]} in the folder {info["file_name"]}'
                    log(self.node_id, log_content)
                    return
                    
                
        tracker_url,info_hash,info = TorrentFile.load_torrent_file(config.directory.node_files_dir + f'node{self.node_id}/' + torrent)
        torrent_data = {
            'announce': tracker_url,
            'info_hash': info_hash,
            'info': info
        }
        message = Node2Tracker(node_id=self.node_id,
                               mode=config.tracker_requests_mode.OWN,
                               info_hash=info_hash,
                               left=0,
                               port=self.rcv_socket.getsockname()[1])
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as send_socket:
            send_socket.connect(tuple(config.constants.TRACKER_ADDR))
            send_socket.sendall(message.encode())
        if self.is_in_send_mode:    # has been already in send(upload) mode
            log_content = f"Some other node also requested a file from you! But you are already in ~(upload) mode!"
            log(node_id=self.node_id, content=log_content)
            return
        else:
            self.is_in_send_mode = True
            log_content = f"You are free now! You are waiting for other nodes' requests!"
            self.torrent_data = torrent_data
            log(node_id=self.node_id, content=log_content)
            t = Thread(target=self.listen)
            t.setName("sending thread")
            t.setDaemon(True)
            t.start()

    def receive_chunk(self, info_hash, range: tuple, file_owner: tuple):
        '''
        Method to receive chunk (a range of pieces) from an owner
        
        Args:
            info_hash: id of torrent file
            range: location of the chunk in the file
            file_owner: a tuple where:
                - [0]: peer info -> {node_id,addr,left}
                - [1]: freq_list
        '''
        dest_node = file_owner[0]
        # we set idx of ChunkSharing to -1, because we want to tell it that we
        # need the chunk from it
        # SENDING A MESSAGE TO REQUEST A CHUNK FROM THE OWNER
        msg = ChunkSharing(src_node_id=self.node_id,
                           dest_node_id=dest_node["node_id"],
                           info_hash=info_hash,
                           range=range)
        
        # temp_port = generate_random_port()
        print(tuple(dest_node['addr']))
        temp_sock = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        temp_sock.connect(tuple(dest_node['addr']))
        temp_sock.settimeout(10)
        temp_sock.sendall(msg.encode())
        log_content = "Asking for chunks of node {0} at address {1}".format(dest_node['node_id'],dest_node['addr'])
        log(node_id=self.node_id, content=log_content)
        
        log_content = "I sent a request for a chunk of {0} for node{1}".format(info_hash, dest_node["node_id"])
        log(node_id=self.node_id, content=log_content)
        piece_size = self.torrent_data['info']['piece_size']
        #WAITING FOR THE OWNER TO ANSWER BACK WITH THE CHUNKS BYTE
        i = 0
        def recv_exact(sock, length):
            data = b''
            while len(data) < length:
                chunk = sock.recv(length - len(data))
                if not chunk:
                    return None  # Connection closed
                data += chunk
            return data
        while True:

            length_data = recv_exact(temp_sock, 4)
            if not length_data:
                log_content = f'Idx{i}: No data received! Closing connection'
                log(node_id=self.node_id,content=log_content)
                return
            
            msg_length = struct.unpack("!I",length_data)[0]

            message = b""
            while len(message) < msg_length:
                chunk = temp_sock.recv(min(msg_length - len(message), config.constants.BUFFER_SIZE))
                if not chunk:
                    log_content = f"Idx {i}: Connection closed before receiving full message."
                    log(node_id=self.node_id, content=log_content)
                    return
                message += chunk 
            # log_content = f"Idx {i}: I have just received a data of {sys.getsizeof(message)} bytes"
            # log(node_id=self.node_id, content=log_content)    
            msg = Message.decode(message)

            if msg["idx"] == -1: # end of the file
                # free_socket(temp_sock)
                temp_sock.close()
                return
            
            i+=1
            self.downloaded_files[info_hash].append(msg)

    def sort_downloaded_chunks(self, info_hash) -> list:
        sort_result_by_range = sorted(self.downloaded_files[info_hash],
                                      key=itemgetter("range"))
        group_by_range = groupby(sort_result_by_range,
                                 key=lambda i: i["range"])
        sorted_downloaded_chunks = []
        for _, value in group_by_range:
            value_sorted_by_idx = sorted(list(value),
                                         key=itemgetter("idx"))
            sorted_downloaded_chunks.append(value_sorted_by_idx)

        return sorted_downloaded_chunks
    def reassemble_folder(self,chunks,folder_path,info):
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
        folder_offset = 0
        piece_size = config.constants.CHUNK_PIECES_SIZE
        for file_info in info['files']:
            file_name = file_info['file_name']
            file_size = file_info['length']
            
            file_path = os.path.join(folder_path,file_name)
            
            with open(file_path,"wb+") as f:
                while file_size > 0:
                    chunk = chunks[folder_offset]
                    if len(chunk) <= file_size:
                        f.write(chunk)
                        file_size -= len(chunk)
                        folder_offset += 1
                    else:
                        f.write(chunk[:file_size])
                        chunks[folder_offset] = chunk[file_size:]
                        file_size = 0
                    
    def split_file_owners(self, file_owners: list, info: dict, info_hash):
        '''
        Method to split the file owners and assign each to a range of pieces.
        
        Args:
            file_owners (list): A list of tuples (peer, freq_list), where:
                - peer: Information about a peer (node_ID, addr, left).
                - freq_list
            info (dict): Torrent info data containing metadata about the file.
            info_hash: Torrent identifier (e.g., unique hash to identify this torrent).
        '''
        owners = []
        # get owners that are seeders
        for owner in file_owners:
            if owner[0]['node_id'] != self.node_id and owner[0]['left'] == 0:
                owners.append(owner)
        # No seeder -> stop
        if len(owners) == 0:
            log_content = f"No one has {info_hash}"
            log(node_id=self.node_id, content=log_content)
            return
        # sort owners based on their sending frequency
        owners = sorted(owners, key=lambda x: x[1], reverse=True)

        to_be_used_owners = owners[:config.constants.MAX_SPLITTNES_RATE]
        #IMPLEMENT LOGIC FOR MULTI FILE OR ONE FILE HERE
        log_content = f"You are going to download {info_hash} from Node(s) {[o[0]['node_id'] for o in to_be_used_owners]}"
        file_size = info['file_size']
        step = file_size / len(to_be_used_owners)
        chunks_ranges = [(round(step*i), round(step*(i+1))) for i in range(len(to_be_used_owners))]

        # 3. Create a thread for each neighbor peer to get a chunk from it
        self.downloaded_files[info_hash] = []
        neighboring_peers_threads = []
        for idx, obj in enumerate(to_be_used_owners):
            t = Thread(target=self.receive_chunk, args=(info_hash, chunks_ranges[idx], obj))
            t.setDaemon(True)
            t.start()
            neighboring_peers_threads.append(t)
        for t in neighboring_peers_threads:
            t.join()

        log_content = "All the chunks of {} has downloaded from neighboring peers. But they must be reassembled!".format(info_hash)
        log(node_id=self.node_id, content=log_content)

        # 4. Now we have downloaded all the chunks of the file. It's time to sort them.
        sorted_chunks = self.sort_downloaded_chunks(info_hash=info_hash)
        log_content = f"All the pieces of the {info_hash} is now sorted and ready to be reassembled."
        log(node_id=self.node_id, content=log_content)

        # 5. Finally, we assemble the chunks to re-build the file
        total_file = []
        file_path = f"{config.directory.node_files_dir}node{self.node_id}/{info['file_name']}"
        for chunk in sorted_chunks:
            for piece in chunk:
                total_file.append(piece["chunk"])
        print(len(chunk))
        if 'files' in info:
            self.reassemble_folder(chunks=total_file,folder_path= file_path,info=info)
        else:
            self.reassemble_file(chunks=total_file,
                             file_path=file_path)
        log_content = f"{info['file_name']} has successfully downloaded and saved in my files directory."
        log(node_id=self.node_id, content=log_content)
        self.files.append(info['file_name'])
    
    def ask_peer_info(self, info_hash, file_owners):
        if len(file_owners) == 0:
            return None
        file_owner = file_owners[0]
        msg = NodeInfo(self.node_id, file_owner[0]['node_id'], info_hash, None)

        temp_sock = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        temp_sock.connect(tuple(file_owner[0]["addr"]))
        temp_sock.settimeout(10)
        temp_sock.send(msg.encode())
        print(temp_sock)
        # temp_port = generate_random_port()
        # temp_sock = set_socket(temp_port)
        # self.send_segment(sock=temp_sock,
        #             data=Message.encode(msg),
        #             addr=tuple(file_owner[0]["addr"]))
        # log_content = "I sent a request for info for node{1}".format(info_hash, file_owner[0]["node_id"])
        # log(node_id=self.node_id, content=log_content)
        data = b""
        while True:
            packet = temp_sock.recv(config.constants.BUFFER_SIZE)
            if not packet: break
            data += packet
        # data = temp_sock.recv(config.constants.BUFFER_SIZE)
        respond_msg = Message.decode(data)
        temp_sock.close()
        return respond_msg['info']
            
    def set_download_mode(self, torrent_file: str):
        is_magnet_text = False
        file_path = ""
        if torrent_file.startswith("magnet:?"):
            is_magnet_text = True
        else:
            file_path = f"{config.directory.node_files_dir}node{self.node_id}/{torrent_file}"
        # check if torrent file is in the node directory
        if not os.path.isfile(file_path) and not is_magnet_text:
            log_content = f"You dont have the torrent file!"
            log(node_id=self.node_id, content=log_content)
            return
        else:
            log_content = f"You just started to download {torrent_file}. Let's search it in torrent!"
            log(node_id=self.node_id, content=log_content)
            # load the torrent file
            if not is_magnet_text:
                tracker_url,info_hash,info = TorrentFile.load_torrent_file(file_path)
                self.torrent_data = {
                    'announce': tracker_url,
                    'info_hash': info_hash,
                    'info': info
                }
                tracker_response = self.search_torrent(info_hash=info_hash,left=len(info['pieces']))
            else:
                tracker_url, info_hash, file_name, file_size = TorrentFile.load_magnet_text(torrent_file)
                # Left here doesnt matter ?
                tracker_response = self.search_torrent(info_hash=info_hash,left=int(file_size))
                info = self.ask_peer_info(info_hash, tracker_response['peers'])
                if info == None:
                    log_content = f"Noone send {info_hash}. Please try again!"
                    log(node_id=self.node_id, content=log_content)
                    return
                self.torrent_data = {
                    'announce': tracker_url,
                    'info_hash': info_hash,
                    'info': info
                }

            # asking tracker for the peers that have the info_hash
            # tracker response should have peers -> {peer -> {}, send_freq_list -> {}}
            # tracker_response = self.search_torrent(info_hash=info_hash,left=len(info['pieces']))
            file_owners = tracker_response['peers']

            self.split_file_owners(file_owners=file_owners,info=info,info_hash=info_hash)
            # report to tracker that we have finished the download

            msg = Node2Tracker(node_id=self.node_id,
                               mode=config.tracker_requests_mode.FIN,
                               info_hash=info_hash,
                               left=0,
                               port=self.rcv_socket.getsockname()[1])
            with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
                s.connect(tuple(config.constants.TRACKER_ADDR))
                s.sendall(msg.encode())
            self.torrent_data = None
    def get_scrape(self,file):
        '''
        Method to scrape the tracker for swarm statistics

        Args:
            file: .torrent or magnet text(not implemented) -> info_hash
        '''
        file_path = config.directory.node_files_dir + f'node{self.node_id}/' + file
        _, info_hash, _ = TorrentFile.load_torrent_file(file_path)
        
        #SEND REQUEST TO TRACKER TO SCRAPE
        scrape_msg = Node2Tracker(node_id=self.node_id,
                                  mode=config.tracker_requests_mode.SCRAPE,
                                  info_hash=info_hash,
                                  left=0,
                                  port=self.rcv_socket.getsockname()[1])
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as send_socket:
            send_socket.connect(tuple(config.constants.TRACKER_ADDR))
            send_socket.sendall(scrape_msg.encode())
            tracker_response = Message.decode(send_socket.recv(config.constants.BUFFER_SIZE))
            
        if not tracker_response['files']:
            log_content = f'There are no {info_hash} in the torrent'
            log(self.node_id, log_content)
            return    
        # printing out scrape
        file_info = tracker_response['files'][info_hash]
        file_data = []
        for cat, val in file_info.items():
            file_data.append(f'{cat}: {val}')
        log_content = "For info hash {0}:\n{1}".format(info_hash,'\n'.join(file_data))
        log(node_id=self.node_id,content=log_content)
        
    def search_torrent(self, info_hash, left) -> dict:
        # send a tracker request
        log(self.node_id,f'Sending request to tracker for {info_hash}')
        msg = Node2Tracker(node_id=self.node_id,
                           mode = config.tracker_requests_mode.NEED,
                           info_hash=info_hash,
                           left = left,
                           port = self.rcv_socket.getsockname()[1])
        
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as search_sock:
            search_sock.connect(tuple(config.constants.TRACKER_ADDR))
            search_sock.sendall(msg.encode())
            # now we must wait for the tracker response
            data = search_sock.recv(config.constants.BUFFER_SIZE)
            
        tracker_msg = Message.decode(data)
        return tracker_msg

    def fetch_owned_files(self) -> list:
        files = []
        node_files_dir = config.directory.node_files_dir + 'node' + str(self.node_id)
        if os.path.isdir(node_files_dir):
            _, _, files = next(os.walk(node_files_dir))
        else:
            os.makedirs(node_files_dir)

        return files

    def exit_torrent(self):
        msg = Node2Tracker(node_id=self.node_id,
                           mode=config.tracker_requests_mode.EXIT,
                           info_hash="",
                           left=-1,
                           port=self.rcv_socket.getsockname()[1])
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as send_socket:
            send_socket.connect(tuple(config.constants.TRACKER_ADDR))
            send_socket.sendall(msg.encode())

        self.rcv_socket.close()

        log_content = f"You exited the torrent!"
        log(node_id=self.node_id, content=log_content)

    def enter_torrent(self):
        msg = Node2Tracker(node_id=self.node_id,
                           mode=config.tracker_requests_mode.REGISTER,
                           info_hash='',
                           left=-1,
                           port=self.rcv_socket.getsockname()[1])
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as send_socket:
            send_socket.connect(tuple(config.constants.TRACKER_ADDR))
            send_socket.sendall(msg.encode())

        log_content = f"You entered Torrent. Receiving port: {self.rcv_socket.getsockname()[1]}"
        log(node_id=self.node_id, content=log_content)
        # time.sleep(2)

    def inform_tracker_periodically(self, interval: int):
        global next_call
        log_content = f"I informed the tracker that I'm still alive in the torrent!"
        log(node_id=self.node_id, content=log_content)
        
            
        msg = Node2Tracker(node_id=self.node_id,
                           mode=config.tracker_requests_mode.REGISTER,
                           info_hash='',
                           left=-1,
                           port=self.rcv_socket.getsockname()[1])
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as send_socket:
            send_socket.connect(tuple(config.constants.TRACKER_ADDR))
            send_socket.sendall(msg.encode())
        next_call = next_call + interval
        Timer(next_call - time.time(), self.inform_tracker_periodically, args=(interval,)).start()
# args.node_id
def run(args):
    node = Node(node_id=args.node_id)
    log_content = f"***************** Node program started just right now! *****************"
    log(node_id=node.node_id, content=log_content)
    node.enter_torrent()

    # We create a thread to periodically informs the tracker to tell it is still in the torrent.
    timer_thread = Thread(target=node.inform_tracker_periodically, args=(config.constants.NODE_TIME_INTERVAL,))
    timer_thread.setName('informTrackerThread')
    timer_thread.setDaemon(True)
    timer_thread.start()

    print("ENTER YOUR COMMAND!")
    while True:
        command = input()
        mode, file = parse_command(command)

        #################### send mode ####################
        if mode == 'send':
            node.set_send_mode(torrent=file)
        #################### download mode ####################
        elif mode == 'download':
            t = Thread(target=node.set_download_mode, args=(file,))
            t.setName('downloading thread')
            t.setDaemon(True)
            t.start()
            
        elif mode == 'scrape':
            t = Thread(target=node.get_scrape,args=(file,))
            t.setDaemon(True)
            t.start()
        
        elif mode == 'createTor':
            file = f"{config.directory.node_files_dir}node{node.node_id}/{file}"
            if not os.path.isfile(file):
                log_content = f"Error: File '{file}' does not exist in node {node.node_id}'s directory."
                log(node_id=node.node_id,content=log_content)
            else:
                torrent_file = TorrentFile(file, True)
                torrent_data = torrent_file.create_torrent_data()
                torrent_file.create_torrent_file(node.node_id, torrent_data)
                log_content = f"Node {node.node_id} has created {torrent_data['info']['file_name']}.torrent"
                log(node_id=node.node_id,content=log_content)
        elif mode == 'createTorMult':
            file = f"{config.directory.node_files_dir}node{node.node_id}/{file}"
            if not os.path.isdir(file):
                log_content = f"Error: Folder '{file}' does not exist in node {node.node_id}'s directory."
                log(node_id=node.node_id,content=log_content)
            else:
                torrent_file = TorrentFile(file, False)
                torrent_data = torrent_file.create_torrent_data()
                torrent_file.create_torrent_file(node.node_id, torrent_data)
                log_content = f"Node {node.node_id} has created {torrent_data['info']['file_name']}.torrent"
                log(node_id=node.node_id,content=log_content)
        elif mode == 'createMag':
            file = f"{config.directory.node_files_dir}node{node.node_id}/{file}"
            if not os.path.isfile(file):
                log_content = f"Error: File '{file}' does not exist in node {node.node_id}'s directory."
                log(node_id=node.node_id,content=log_content)
            else:
                torrent_file = TorrentFile(file, True)
                torrent_data = torrent_file.create_torrent_data()
                magnet_text = torrent_file.create_magnet_text(torrent_data)
                log_content = f"Node {node.node_id} has created magnet text {magnet_text}"
                log(node_id=node.node_id,content=log_content)

        elif mode == 'scrape':
            t = Thread(target=node.get_scrape,args=(file,))
            t.setDaemon(True)
            t.start()
        #################### exit mode ####################
        elif mode == 'exit':
            node.exit_torrent()
            # time.sleep(2)
            exit(0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-node_id', type=int,  help='id of the node you want to create')
    node_args = parser.parse_args()

    # run the node
    run(args=node_args)
