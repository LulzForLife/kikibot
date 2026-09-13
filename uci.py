import sys
import bulletchess as chess
import main
import os
import time
import threading

TIME_LIMIT = main.TIME_LIMIT
MOVE_OVERHEAD = 0

class DualOutputStream:
    def __init__(self, log_file, original_stream, prefix=""):
        self.log_file = log_file
        self.original_stream = original_stream
        self.prefix = prefix

    def write(self, data):
        self.original_stream.write(data)

        if data:
            lines = data.splitlines(keepends=True)
            for line in lines:
                if line.strip():
                    self.log_file.write(f"{self.prefix}{line}")
                else:
                    self.log_file.write(line)
        self.flush()

    def flush(self):
        self.log_file.flush()
        self.original_stream.flush()

    def reconfigure(self, *args, **kwargs):
        return self.original_stream.reconfigure(*args, **kwargs)

class DualInputStream:
    def __init__(self, log_file, original_stream, prefix=""):
        self.log_file = log_file
        self.original_stream = original_stream
        self.prefix = prefix

    def readline(self, *args, **kwargs):
        data = self.original_stream.readline(*args, **kwargs)
        if data:
            self.log_file.write(f"{self.prefix}{data}")
            self.log_file.flush()
        return data

    def read(self, *args, **kwargs):
        data = self.original_stream.read(*args, **kwargs)
        if data:
            self.log_file.write(data)
            self.log_file.flush()
        return data

f = open("output.log", "w", encoding="utf-8")
f.write("")

sys.stdout = DualOutputStream(f, sys.__stdout__, prefix="[OUT] ")
sys.stderr = DualOutputStream(f, sys.__stderr__, prefix="[ERR] ")
sys.stdin = DualInputStream(f, sys.__stdin__, prefix="[IN ] ")

def parse_position(board: chess.Board, tokens: list[str]) -> chess.Board:
    if not tokens:
        return board

    if tokens[0] == "startpos":
        board = chess.Board()
        remaining_tokens = tokens[1:]
    elif tokens[0] == "fen":
        if "moves" in tokens:
            moves_idx = tokens.index("moves")
            fen_str = " ".join(tokens[1:moves_idx])
            remaining_tokens = tokens[moves_idx:]
        else:
            fen_str = " ".join(tokens[1:])
            remaining_tokens = []
        board = chess.Board.from_fen(fen_str)
    else:
        return board

    if remaining_tokens and remaining_tokens[0] == "moves":
        for move_str in remaining_tokens[1:]:
            try:
                board.apply(chess.Move.from_uci(move_str))
            except ValueError:
                continue
    return board

def get_time(tokens, board) -> tuple[float, int]:
    wtime = btime = winc = binc = movestogo = movetime = depth = None
    infinite = False

    iterator = iter(tokens)
    for token in iterator:
        try:
            if token == "wtime": wtime = int(next(iterator))
            elif token == "btime": btime = int(next(iterator))
            elif token == "winc": winc = int(next(iterator))
            elif token == "binc": binc = int(next(iterator))
            elif token == "movestogo": movestogo = int(next(iterator))
            elif token == "movetime": movetime = int(next(iterator))
            elif token == "depth": depth = int(next(iterator))
            elif token == "infinite": infinite = True
        except StopIteration:
            break

    if movetime is not None:
        time_limit = movetime / 1000.0
    elif infinite:
        time_limit = 86400
    else:
        my_time = wtime if board.turn == chess.WHITE else btime
        my_inc = winc if board.turn == chess.WHITE else binc

        if my_time is not None:
            my_time_sec = my_time / 1000.0
            my_inc_sec = (my_inc / 1000.0) if my_inc is not None else 0.0

            if movestogo is not None:
                time_limit = (my_time_sec / max(1, movestogo)) + my_inc_sec
            else:

                time_limit = (my_time_sec / 35.0) + my_inc_sec

            time_limit = max(0.02, min(time_limit, my_time_sec * 0.85))
        else:
            time_limit = None

    if depth is not None:
        max_depth = depth
        if time_limit is None:
            time_limit = 86400
    else:
        max_depth = 100
        if time_limit is None:
            time_limit = 3.0

    return time_limit, max_depth

def parse_go(board: chess.Board, tokens: list[str], is_ponder: bool = False) -> None:
    def run_and_print(board: chess.Board, time_limit: float, max_depth: int) -> None:
        best_move, _ = main.get_best_move(board, time_limit, max_depth)
        if best_move is None:
            best_move, _ = main.get_best_move(board, 86400, 1)
        if best_move is None:
            raise ValueError
        try:
            ponder_move = main.get_pv(board, best_move).split()[1]
            board.apply(best_move)
            board.apply(chess.Move.from_uci(ponder_move))
            if board in chess.CHECKMATE or board in chess.DRAW:
                raise IndexError
            board.undo()
            board.undo()
            print(f"bestmove {best_move.uci()} ponder {ponder_move}", flush=True)
        except IndexError:
            print(f"bestmove {best_move.uci()}")
    global TIME_LIMIT

    time_limit, max_depth = get_time(tokens, board)
    TIME_LIMIT = time_limit

    main.PONDER = is_ponder

    b = board.copy()
    thread = threading.Thread(
        target=run_and_print,
        args=(b, TIME_LIMIT - (MOVE_OVERHEAD / 1000), max_depth),
        daemon=True
    )
    thread.start()

def parse_setoption(parts: list[str]) -> None:
    global MOVE_OVERHEAD
    name = parts[1]
    if name == "Clear_Hash":
        main.tt.clear()
        main.clear_killer()
        main.history_white.clear()
        main.history_black.clear()
        main.counter_moves.clear()
        return
    value = parts[3]
    if name == "Opening_Book":
        if not os.path.exists("komodo.bin"):
            print("info string ./komodo.bin not found")
        if value == "true":
            main.USE_OPENING = True
            print("info string Enabled opening book", flush=True)
        elif value == "false":
            main.USE_OPENING = False
            print("info string Disabled opening book", flush=True)
    elif name == "Use_Syzygy":
        if not os.path.exists("syzygy"):
            print("info string ./syzygy not found", flush=True)
        if value == "true":
            main.USE_SYZYGY = True
            print("info string Enabled syzygy", flush=True)
        elif value == "false":
            main.USE_SYZYGY = False
            print("info string Disabled syzygy", flush=True)
    elif name == "Move_Overhead_MS":
        ms = int(value)
        MOVE_OVERHEAD = ms

def uci_loop() -> None:
    sys.stdout.reconfigure(line_buffering=True) # type: ignore
    board = chess.Board()

    main.PRINT_MODE = "UCI"
    main.USE_OPENING = False
    main.USE_SYZYGY = False
    main.CLEAN_TT = False

    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        parts = line.split()
        if not parts:
            continue

        cmd = parts[0]
        if cmd == "uci":
            print("id name KikiBot")
            print("id author kiranmjlowe")
            print("option name Opening_Book type check default false")
            print("option name Use_Syzygy type check default false")
            print("option name Clear_Hash type button")
            print("option name Move_Overhead_MS type spin default 0 min 0 max 1000")
            print("uciok", flush=True)
        elif cmd == "isready":
            print("readyok", flush=True)
        elif cmd == "ucinewgame":
            main.tt.clear()
            main.clear_killer()
            main.history_white.clear()
            main.history_black.clear()
            main.counter_moves.clear()
            board = chess.Board()
        elif cmd == "position":
            board = parse_position(board, parts[1:])
        elif cmd == "setoption":
            parse_setoption(parts[1:])
        elif cmd == "ponderhit":
            main.END = time.perf_counter() + TIME_LIMIT
        elif cmd == "stop":
            main.END = 0.0
        elif cmd == "go":
            if len(parts) >= 2 and parts[1] == "ponder":
                parse_go(board, parts[2:], True)
            else:
                parse_go(board, parts[1:], False)
        elif cmd == "quit":
            break

if __name__ == "__main__":
    uci_loop()
