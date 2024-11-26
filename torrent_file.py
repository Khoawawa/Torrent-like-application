import bencodepy
import math
import os
import mmap
import hashlib

from configs import CFG,Config
config = Config.from_json(CFG)

class TorrentFile:
    def __init__(self,file_name=None,file_size=None,file_path=None,file_mode=None):
        
        self.file_name = file_name
        self.file_size = file_size
        self.piece_size = config.constants.CHUNK_PIECES_SIZE
        self.mode = file_mode # True == Single_File_Mode else Multi_File_Mode

        piece_list = self.split_file_to_piece(file_path, (0, self.file_size))
        self.pieces = self.hash_piece_and_concatenate(piece_list)

    def _calculate_piece_nums(self) -> int:
        return math.ceil(self.file_size / self.piece_size)
    
    def create_torrent_data(self):
        info = {
            'file_name': self.file_name,
            'pieces': self.pieces,
            'piece_size': self.piece_size,
            'file_size': self.file_size
        }
        info_bencoded = bencodepy.encode(info)
        return {
            'announce': f'https://{config.constants.TRACKER_ADDR[0]}:{config.constants.TRACKER_ADDR[1]}'.encode('utf-8'),
            'info_hash': hashlib.sha1(info_bencoded).hexdigest(),
            'info': info
        }
    
    def create_torrent_file(self, pid, torrent_data):
        torrent_file_content = bencodepy.encode(torrent_data)
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
            f"&tr={tracker_url}"
        )
        return magnet_link

    def split_file_to_piece(self, file_path: str, rng: tuple):
        with open(file_path, "r+b") as f:
            mm = mmap.mmap(f.fileno(), 0)[rng[0]: rng[1]]
            # we divide each chunk to a fixed-size pieces to be transferable
            piece_size = config.constants.CHUNK_PIECES_SIZE
            return [mm[p: p + piece_size] for p in range(0, rng[1] - rng[0], piece_size)]
        
    def hash_piece_and_concatenate(self, piece_list):
        pieces = []
        for piece in piece_list:
            piece_hash = hashlib.sha1(piece).hexdigest()
            pieces.append(piece_hash)
        return pieces

    def load_torrent_file(self, torrent_file_path):
        with open(torrent_file_path, 'rb') as file:
            torrent_data = file.read()

        torrent_data = self.decode_utf8(bencodepy.decode(torrent_data))
        return torrent_data['announce'], torrent_data['info_hash'], torrent_data['info']
    
    def decode_utf8(self,data):
        if isinstance(data, dict):
            return {self.decode_utf8(key): self.decode_utf8(value) for key, value in data.items()}
        elif isinstance(data, list):
            return [self.decode_utf8(item) for item in data]
        elif isinstance(data, bytes):
            return data.decode('utf-8')  # Decode bytes to string
        else:
            return data
# Testing
if __name__ == "__main__":
    file_name = 'file_A.txt'
    file_path = config.directory.node_files_dir + 'node1/' + file_name
    file_size = os.path.getsize(file_path)
    torrentFile = TorrentFile(file_name, file_size, file_path, True)
    torrent_data = torrentFile.create_torrent_data()
    torrentFile.create_torrent_file(1, torrent_data)