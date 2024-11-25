# built-in libraries
from threading import Thread, Timer
from collections import defaultdict
import json
import datetime
import time
import warnings
warnings.filterwarnings("ignore")

# implemented classes
from utils import *
from messages.message import  Message
from messages.tracker2node import Tracker2Node, TrackerScrape2Node
from segment import UDPSegment
from configs import CFG, Config
config = Config.from_json(CFG)

next_call = time.time()

class Tracker:
    def __init__(self):
        self.tracker_socket = set_socket(config.constants.TRACKER_ADDR[1])
        self.tracker_socket.listen(5)
        self.file_owners_list = defaultdict(list)
        self.send_freq_list = defaultdict(int)
        self.has_informed_tracker = defaultdict(bool)

    def send_segment(self, sock: socket.socket, data: bytes, addr: tuple,):
        sock.connect(addr)
        sock.send(data)

    def add_file_owner(self, msg: dict, addr: tuple):
        entry = {
            'node_id': msg['node_id'],
            'addr': (addr[0],msg['port']),
            'left': msg['left']
        }
        log_content = f"Node {msg['node_id']} owns {msg['info_hash']}"
        log(node_id=0, content=log_content, is_tracker=True)

        self.file_owners_list[msg['info_hash']].append(json.dumps(entry))
        self.file_owners_list[msg['info_hash']] = list(set(self.file_owners_list[msg['info_hash']]))
        self.send_freq_list[msg['node_id']] += 1
        self.send_freq_list[msg['node_id']] -= 1

        self.save_db_as_json()

    def update_db(self, msg: dict):
        self.send_freq_list[msg["node_id"]] += 1
        self.save_db_as_json()

    def search_file(self, msg: dict, addr: tuple):
        '''
        Method to search for peers list who own the info_hash  (i.e the original file)
        '''
        
        log_content = f"Node{msg['node_id']} is searching for {msg['info_hash']}"
        log(node_id=0, content=log_content, is_tracker=True)

        matched_entries = []
        for json_entry in self.file_owners_list[msg['info_hash']]:
            entry = json.loads(json_entry)
            matched_entries.append((entry, self.send_freq_list[entry['node_id']]))
        
        tracker_response = Tracker2Node(node_id = msg['node_id'],
                                        peers = matched_entries)

        self.send_segment(sock=self.tracker_socket,
                          data=tracker_response.encode(),
                          addr=addr)

    def remove_node(self, node_id: int, addr: tuple):
        entry = {
            'node_id': node_id,
            'addr': addr
        }
        try:
            self.send_freq_list.pop(node_id)
        except KeyError:
            pass
        self.has_informed_tracker.pop((node_id, addr))
        node_files = self.file_owners_list.copy()
        for nf in node_files:
            if json.dumps(entry) in self.file_owners_list[nf]:
                self.file_owners_list[nf].remove(json.dumps(entry))
            if len(self.file_owners_list[nf]) == 0:
                self.file_owners_list.pop(nf)

        self.save_db_as_json()

    def check_nodes_periodically(self, interval: int):
        global next_call
        alive_nodes_ids = set()
        dead_nodes_ids = set()
        try:
            for node, has_informed in self.has_informed_tracker.items():
                node_id, node_addr = node[0], node[1]
                if has_informed: # it means the node has informed the tracker that is still in the torrent
                    self.has_informed_tracker[node] = False
                    alive_nodes_ids.add(node_id)
                else:
                    dead_nodes_ids.add(node_id)
                    self.remove_node(node_id=node_id, addr=node_addr)
        except RuntimeError: # the dictionary size maybe changed during iteration, so we check nodes in the next time step
            pass

        if not (len(alive_nodes_ids) == 0 and len(dead_nodes_ids) == 0):
            log_content = f"Node(s) {list(alive_nodes_ids)} is in the torrent and node(s){list(dead_nodes_ids)} have left."
            log(node_id=0, content=log_content, is_tracker=True)

        datetime.now()
        next_call = next_call + interval
        Timer(next_call - time.time(), self.check_nodes_periodically, args=(interval,)).start()

    def save_db_as_json(self):
        if not os.path.exists(config.directory.tracker_db_dir):
            os.makedirs(config.directory.tracker_db_dir)

        nodes_info_path = config.directory.tracker_db_dir + "nodes.json"
        files_info_path = config.directory.tracker_db_dir + "files.json"

        # saves nodes' information as a json file
        temp_dict = {}
        for key, value in self.send_freq_list.items():
            temp_dict['node'+str(key)] = value
        nodes_json = open(nodes_info_path, 'w')
        json.dump(temp_dict, nodes_json, indent=4, sort_keys=True)

        # saves files' information as a json file
        files_json = open(files_info_path, 'w')
        json.dump(self.file_owners_list, files_json, indent=4, sort_keys=True)
    def scrape(self,msg,addr):
        peers = self.file_owners_list[msg['info_hash']]
        complete = 0
        incomplete = 0
        for peer in peers:
            if peer['left'] == 0:
                complete += 1
            else:
                incomplete += 1
        files = {
            msg['info_hash']: {
                'complete' : complete,
                'incomplete': incomplete
            }
        }
        msg = TrackerScrape2Node(node_id=msg['node_id'],
                                 files=files)
        
        self.send_segment(sock=self.tracker_socket,
                          data=msg.encode(),
                          addr=addr)
    def update_stat(self,msg,addr):
        info_hash = msg['info_hash']
        if info_hash == '':
            return
        entry_found = False
        updated_list = []
        for json_entry in self.file_owners_list[msg['info_hash']]:
            entry = json.loads(json_entry)
            if entry['node_id'] == msg['node_id']:
                if msg['left'] != -1: 
                    entry['left'] = msg['left']
                    entry_found = True
            updated_list.append(json.dumps(entry))
            
        if not entry_found:
            self.add_file_owner(msg,addr)
            return
        
        log_content = f"Updated info_hash {info_hash} for node{msg['node_id']}"
        log(0,log_content,is_tracker=True)
        
        self.file_owners_list[info_hash] = list(set(updated_list))
        self.save_db_as_json()
        
         
            
    def handle_node_request(self, data: bytes, addr: tuple):
        msg = Message.decode(data)
        mode = msg['mode']
        if mode == config.tracker_requests_mode.OWN:
            self.add_file_owner(msg=msg, addr=addr)
        elif mode == config.tracker_requests_mode.NEED:
            self.search_file(msg=msg, addr=addr)
        elif mode == config.tracker_requests_mode.UPDATE:
            self.update_db(msg=msg)
        elif mode == config.tracker_requests_mode.REGISTER:
            self.has_informed_tracker[(msg['node_id'], addr)] = True
            self.update_stat(msg=msg,addr=addr)
        elif mode == config.tracker_requests_mode.SCRAPE:
            self.scrape(msg=msg, addr=addr)
        elif mode == config.tracker_requests_mode.EXIT:
            self.remove_node(node_id=msg['node_id'], addr=addr)
            log_content = f"Node {msg['node_id']} exited torrent intentionally."
            log(node_id=0, content=log_content, is_tracker=True)

    def listen(self):
        timer_thread = Thread(target=self.check_nodes_periodically, args=(config.constants.TRACKER_TIME_INTERVAL,))
        timer_thread.setDaemon(True)
        timer_thread.start()
        while True:
            conn, addr = self.tracker_socket.accept()
            log(0,f'Connected by {addr}',True)
            with conn:
                data = conn.recv(config.constants.BUFFER_SIZE)
                t = Thread(target=self.handle_node_request,args=(data, addr))
                t.start()

    def run(self):
        log_content = f"***************** Tracker program started just right now! *****************"
        log(node_id=0, content=log_content, is_tracker=True)
        t = Thread(target=self.listen)
        t.daemon = True
        t.start()
        t.join()

if __name__ == '__main__':
    t = Tracker()
    t.run()