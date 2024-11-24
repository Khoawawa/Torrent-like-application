from messages.message import Message

class Tracker2Node(Message):
    def __init__(self, node_id: int, peers: list):

        super().__init__()
        self.node_id = node_id
        self.peers = peers
class TrackerScrape2Node(Message):
    def __init__(self,node_id: int, files: dict):
        super().__init__()
        self.node_id = node_id
        self.files = files