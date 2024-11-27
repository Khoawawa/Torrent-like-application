import bencodepy
import math
import os
import mmap
import hashlib
import urllib.parse

from configs import CFG,Config
config = Config.from_json(CFG)

class TorrentFile:
    def __init__(self,file_path=None,file_mode=None):
        self.file_mode = file_mode # True == Single_File_Mode else Multi_File_Mode
        self.folder_path = None
        self.file_name = os.path.basename(file_path)
        self.piece_size = config.constants.CHUNK_PIECES_SIZE
        if self.file_mode:
            self.file_size = self._calculate_file_size(file_path)
            piece_list = self.split_file_to_piece(file_path, self.file_size)
            self.pieces = self.hash_piece_and_concatenate(piece_list)
        else:
            self.files = self._create_file_list(file_path)
            piece_list = self.split_files_in_folder(file_path)
            self.pieces = self.hash_piece_to_dict(piece_list)

    def _calculate_piece_nums(self) -> int:
        return math.ceil(self.file_size / self.piece_size)
    
    def _calculate_file_size(self, file_path):
        return os.path.getsize(file_path)
    
    def _create_file_list(self, folder_path):
        file_info = []
        for dirpath, dirnames, filenames in os.walk(folder_path):
            self.folder_path = dirpath
            for filename in filenames:
                file_path = os.path.join(dirpath, filename)
                if os.path.isfile(file_path):
                    file_info.append({'file_name': filename, 'length': os.path.getsize(file_path)})
        return file_info

    def create_torrent_data(self):
        if self.file_mode:
            info = {
            'file_name': self.file_name,
            'pieces': self.pieces,
            'piece_size': self.piece_size,
            'file_size': self.file_size
        }
        else:
            info = {
                'file_name': self.file_name, # Tui đặt tên cho khớp trên thôi, này folder name
                'files': self.files,
                'file_size': sum(file['length'] for file in self.files),
                'pieces': [piece for pieces in self.pieces.values() for piece in pieces],
                'piece_size': self.piece_size,
            }
        info_bencoded = bencodepy.encode(info)
        return {
            'announce': f'https://{config.constants.TRACKER_ADDR[0]}:{config.constants.TRACKER_ADDR[1]}'.encode('utf-8'),
            'info_hash': hashlib.sha1(info_bencoded).hexdigest(),
            'info': info
        }
    
    def create_torrent_file(self, pid, torrent_data):
        torrent_file_content = bencodepy.encode(torrent_data)
        if self.file_mode:
            file_name = torrent_data['info']['file_name']
        else:
            file_name = torrent_data['info']['file_name']
        torrent_file_path = os.path.join(
            config.directory.node_files_dir, f'node{pid}',
            f'{file_name}.torrent'
        )
        with open(torrent_file_path,'wb') as f:
            f.write(torrent_file_content)
    
    def create_magnet_text(self, torrent_data):
        tracker_url = torrent_data['announce'].decode('utf-8')
        info = torrent_data['info']
        info_bencoded = bencodepy.encode(info)
        magnet_link = (
            f"magnet:?xt=urn:btih:{hashlib.sha1(info_bencoded).hexdigest()}"
            f"&dn={info['file_name']}"
            f"&xl={info['file_size']}"
            f"&tr={tracker_url}"
        )
        return magnet_link

    def split_file_to_piece(self, file_path: str, file_size):
        with open(file_path, "r+b") as f:
            mm = mmap.mmap(f.fileno(), 0)[0: file_size]
            # we divide each chunk to a fixed-size pieces to be transferable
            return [mm[p: p + self.piece_size] for p in range(0, file_size, self.piece_size)]

    def split_files_in_folder(self, folder_path):
        file_pieces = {}
        for file_name in os.listdir(folder_path):
            file_path = os.path.join(folder_path, file_name)
            if os.path.isfile(file_path):  # Ensure it's a file
                file_pieces[file_name] = self.split_file_to_piece(file_path, os.path.getsize(file_path))
        return file_pieces

    def hash_piece_and_concatenate(self, piece_list):
        pieces = []
        for piece in piece_list:
            if isinstance(piece, str):
                piece = piece.encode()  # Convert string to bytes

            piece_hash = hashlib.sha1(piece).hexdigest()
            pieces.append(piece_hash)
        return pieces

    def hash_piece_to_dict(self, piece_list):
        hashed_dict = {}
        for filename, pieces in piece_list.items():
            hashed_pieces = self.hash_piece_and_concatenate(pieces)
            hashed_dict[filename] = hashed_pieces
        
        return hashed_dict
    @staticmethod
    def load_torrent_file(torrent_file_path):
        with open(torrent_file_path, 'rb') as file:
            torrent_data = file.read()

        torrent_data = TorrentFile.decode_utf8(bencodepy.decode(torrent_data))
        return torrent_data['announce'], torrent_data['info_hash'], torrent_data['info']
    @staticmethod
    def load_magnet_text(magnet_link):
        parsed = urllib.parse.urlparse(magnet_link)
        query_params = urllib.parse.parse_qs(parsed.query)

        # Extract info hash (xt parameter)
        info_hash = None
        if 'xt' in query_params:
            xt_values = query_params['xt']
            for xt in xt_values:
                if xt.startswith('urn:btih:'):
                    info_hash = xt.split('urn:btih:')[1]
                    break
    
        # Extract file name (dn parameter)
        file_name = query_params.get('dn', [None])[0]
    
        # Extract tracker URL (tr parameter)
        tracker_url = query_params.get('tr', [None])[0]

        file_size = query_params.get('xl', [None])[0]

        return tracker_url, info_hash, file_name, file_size
    @staticmethod
    def decode_utf8(data):
        if isinstance(data, dict):
            return {TorrentFile.decode_utf8(key): TorrentFile.decode_utf8(value) for key, value in data.items()}
        elif isinstance(data, list):
            return [TorrentFile.decode_utf8(item) for item in data]
        elif isinstance(data, bytes):
            return data.decode('utf-8')  # Decode bytes to string
        else:
            return data
# Testing
# torrentFile = TorrentFile('node_files\\node1\\file_A.txt', True)
# torrent_data = torrentFile.create_torrent_data()
# # torrentFile.create_torrent_file(5, torrent_data)

torrentFile = TorrentFile('node_files\\node1\\ABC', False)
# # # # torrentFile = TorrentFile('node_files\\node1', False)
torrent_data = torrentFile.create_torrent_data()
torrentFile.create_torrent_file(1, torrent_data)
# # # torrentFile.load_torrent_file('node_files\\node5\\ABC.torrent')
# print(torrentFile.create_magnet_text(torrent_data))
# a,b,c,d = torrentFile.load_magnet_text(torrentFile.create_magnet_text(torrent_data))
# print(a,b,c,d)
