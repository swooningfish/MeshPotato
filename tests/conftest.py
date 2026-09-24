import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The tests don't talk to a radio, so a stand-in meshcore module is enough
# when the real package isn't installed.
try:
    import meshcore  # noqa: F401
except ImportError:
    stub = types.ModuleType("meshcore")
    stub.MeshCore = object
    stub.EventType = types.SimpleNamespace(ERROR="error", CHANNEL_MSG_RECV="chan",
                                           CONTACT_MSG_RECV="dm", RX_LOG_DATA="rxlog")
    sys.modules["meshcore"] = stub
