from messages.message import Message

class ChunkSharing(Message):
    def __init__(self, src_node_id: int, dest_node_id: int, info_hash: str,
                 range: tuple, idx: int =-1, chunk: bytes = None, file_name: str = None):

        super().__init__()
        self.src_node_id = src_node_id
        self.dest_node_id = dest_node_id
        self.info_hash = info_hash
        self.range = range
        self.idx = idx
        self.chunk = chunk
        self.file_name = file_name
