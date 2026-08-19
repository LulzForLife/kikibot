import ctypes
import os.path

file_path = os.path.dirname(__file__)

try:
    _nnue = ctypes.CDLL(os.path.join(file_path, "libnnueprobe.dll"))
except OSError:
    _nnue = ctypes.CDLL(os.path.join(file_path, "libnnueprobe.so"))

_nnue.nnue_init.argtypes = [ctypes.c_char_p]
_nnue.nnue_init.restype = None

_nnue.nnue_init(
    os.path.join(file_path, "nn-baeb9ef2d183.nnue").encode("utf-8")
)

_nnue.nnue_evaluate_fen.argtypes = [ctypes.c_char_p]
_nnue.nnue_evaluate_fen.restype = ctypes.c_int

def nnue_evaluate_fen(fen: str) -> int:
    return _nnue.nnue_evaluate_fen(fen.encode("utf-8"))
