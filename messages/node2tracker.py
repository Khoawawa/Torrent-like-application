from messages.message import Message

class Node2Tracker(Message):
    def __init__(self, node_id: int, mode: int, info_hash: str,left:int, port:int):

        super().__init__()
        self.node_id = node_id
        self.info_hash = info_hash
        self.mode = mode
        self.left = left
        self.port = port
