"""Main-process wall deadlines for explicitly authorized paid follow-up turns."""
import sys
from .conversation import Conversation

if __name__ == '__main__':
    Conversation(sys.argv[3]).run(sys.argv[1], sys.argv[2])
