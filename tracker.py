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
from threading import Lock
config = Config.from_json(CFG)

next_call = time.time()

class Tracker:
    def __init__(self):
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        self.scrape_lock = Lock()
        self.tracker_socket = set_socket(config.constants.TRACKER_ADDR[1],ip)
        self.tracker_socket.listen(5)
        print(self.tracker_socket.getsockname())
        self.file_owners_list = defaultdict(list)
        self.send_freq_list = defaultdict(int)
        self.has_informed_tracker = defaultdict(bool)
        self.scrape = defaultdict(dict)
    def send_segment(self, sock: socket.socket, data: bytes, addr: tuple,):
        sock.sendall(data)
    def init_scrape(self,info_hash):
        if info_hash in self.scrape:
            return
        entry = {
            'complete' : 0,
            'incomplete' : 0,
            'downloaded' : 0,
        }
        self.scrape[info_hash] = entry
            
    def add_file_owner(self, msg: dict, addr: tuple,is_seed = 1):
        entry = {
            'node_id': msg['node_id'],
            'addr': (addr[0],msg['port']),
            'left': msg['left'],
            'is_seed': is_seed
        }
        with self.scrape_lock:
            self.init_scrape(msg['info_hash'])
            if is_seed == -1:
                self.scrape[msg['info_hash']]['incomplete'] += 1

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

    def search_file(self, msg: dict, addr: tuple, conn_socket):
        '''
        Method to search for peers list who own the info_hash  (i.e the original file)
        '''
        
        log_content = f"Node{msg['node_id']} is searching for {msg['info_hash']}"
        log(node_id=0, content=log_content, is_tracker=True)

        matched_entries = []
        for json_entry in self.file_owners_list[msg['info_hash']]:
            entry = json.loads(json_entry)
            if entry['is_seed'] == 1:
                matched_entries.append((entry, self.send_freq_list[entry['node_id']]))
        self.add_file_owner(msg,addr,-1)
        tracker_response = Tracker2Node(node_id = msg['node_id'],
                                        peers = matched_entries)
        self.send_segment(sock=conn_socket,
                          data=tracker_response.encode(),
                          addr=addr)

    def remove_node(self, node_id: int, addr: tuple):
        try:
            entry = None
            for node in self.has_informed_tracker:
                if node[0] == node_id:
                    entry = {
                        'node_id': node[0],
                        'addr': node[1]
                    }
            if entry is None:
                raise Exception()
        except:
            print("No entry for {0} was found".format(node_id))
        try:
            self.send_freq_list.pop(node_id)
        except KeyError:
            pass
        self.has_informed_tracker.pop((entry['node_id'], entry['addr']))
        node_files = self.file_owners_list.copy()
        for nf in node_files:
            for json_entry in node_files[nf]:
                node_entry = json.loads(json_entry)
                if node_entry['node_id'] == entry['node_id'] and tuple(node_entry['addr']) == entry['addr']:
                    self.file_owners_list[nf].remove(json.dumps(node_entry))
                    break
                    
            if len(self.file_owners_list[nf]) == 0:
                self.file_owners_list.pop(nf)

        self.save_db_as_json()

    def check_nodes_periodically(self, interval: int):
        global next_call
        print(f'I am on addr{self.tracker_socket.getsockname()}')
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
        
    def get_scrape(self,msg,addr,conn_socket):
        
        with self.scrape_lock:
            if not len(self.scrape[msg['info_hash']]):
                files = {}
            else:
                files = {
                msg['info_hash'] : self.scrape[msg['info_hash']]
                }
        log_content = "returning scrape data to {0}".format(msg['node_id'])
        log(0,log_content,True)
        msg = TrackerScrape2Node(node_id=msg['node_id'],
                                 files=files)
        
        self.send_segment(sock=conn_socket,
                          data=msg.encode(),
                          addr=addr)
        
    def tell_finish(self,msg,addr):
        updated_list = []
        
        for json_entry in self.file_owners_list[msg['info_hash']]:
            entry = json.loads(json_entry)
            if entry['node_id'] == msg['node_id']:
                with self.scrape_lock:
                    self.scrape[msg['info_hash']]['downloaded'] += entry['left']
                    self.scrape[msg['info_hash']]['complete'] += 1
                    self.scrape[msg['info_hash']]['incomplete'] -= 1
                entry['left'] = 0
                entry['is_seeder'] = 0
            updated_list.append(json.dumps(entry))
            
        self.file_owners_list[msg['info_hash']] = list(set(updated_list))
        
        self.save_db_as_json()
            
    def handle_node_request(self, data: bytes, addr: tuple, conn_socket):
        msg = Message.decode(data)
        mode = msg['mode']
        if mode == config.tracker_requests_mode.OWN:
            self.add_file_owner(msg=msg, addr=addr)
        elif mode == config.tracker_requests_mode.NEED:
            self.search_file(msg=msg, addr=addr, conn_socket=conn_socket)
        elif mode == config.tracker_requests_mode.UPDATE:
            self.update_db(msg=msg)
        elif mode == config.tracker_requests_mode.REGISTER:
            self.has_informed_tracker[(msg['node_id'], (addr[0],msg['port']))] = True
        elif mode == config.tracker_requests_mode.SCRAPE:
            self.get_scrape(msg=msg, addr=addr,conn_socket=conn_socket)
        elif mode == config.tracker_requests_mode.FIN:
            self.tell_finish(msg=msg,addr=addr)
        elif mode == config.tracker_requests_mode.EXIT:
            self.remove_node(node_id=msg['node_id'], addr=addr)
            log_content = f"Node {msg['node_id']} exited torrent intentionally."
            log(node_id=0, content=log_content, is_tracker=True)
        conn_socket.close()

    def listen(self):
        timer_thread = Thread(target=self.check_nodes_periodically, args=(config.constants.TRACKER_TIME_INTERVAL,))
        timer_thread.setDaemon(True)
        timer_thread.start()
        while True:
            conn, addr = self.tracker_socket.accept()
            log(0,f'Connected by {addr}',True)
            data = conn.recv(config.constants.BUFFER_SIZE)
            t = Thread(target=self.handle_node_request,args=(data, addr,conn))
            t.start()

    def run(self):
        log_content = f"***************** Tracker program started just right now! *****************"
        log(node_id=0, content=log_content, is_tracker=True)
        t = Thread(target=self.listen)
        t.setDaemon(True)
        t.start()
        t.join()

if __name__ == '__main__':
    t = Tracker()
    t.run()