import bulletchess as chess
import chess as c
import chess.polyglot as polyglot
import chess.syzygy as syzygy

import nnue

from time import perf_counter
from dataclasses import dataclass

from typing import cast

SELF_PLAY = False
BOT_STARTS = False
USE_OPENING = False
USE_SYZYGY = True

TIME_LIMIT = 10.0
MAX_DEPTH = 10

INF = 100000
INF_THRESHHOLD = 97500
TABLEBASE_INF = 95000
TABLEBASE_INF_THRESHHOLD = 90000

EXACT = 0
UPPER = 1
LOWER = 2

nodes = 0
tt: dict[str, TTEntry] = {}

tablebase = syzygy.Tablebase()
try:
    tablebase.add_directory("syzygy")
except FileNotFoundError:
    del tablebase
    USE_SYZYGY = False

opening_book = polyglot.open_reader("komodo.bin")

@dataclass(slots=True)
class TTEntry:
    score: float
    depth: int
    flag: int
    move: chess.Move | None
    age: int

def get_user_move(b: chess.Board) -> chess.Move:
    legal_moves = b.legal_moves()
    while True:
        try:
            move = chess.Move.from_uci(input("Enter move (e.g. e2e4): "))
            assert move is not None
            assert move in legal_moves
            break
        except (ValueError, AssertionError):
            ...
    return move

def get_best_tablebase_move(b: chess.Board) -> tuple[chess.Move, float]:

    c_board = c.Board(b.fen())

    best_wdl = -INF
    best_dtz = INF
    best_move = None

    for move in c_board.legal_moves:

        c_board.push(move)

        wdl = -tablebase.probe_wdl(c_board)
        dtz = -tablebase.probe_dtz(c_board)

        c_board.pop()

        if wdl > best_wdl:
            best_wdl = wdl
            best_dtz = dtz
            best_move = move
        elif wdl == best_wdl and dtz < best_dtz:
            best_dtz = dtz
            best_move = move

    best_move = cast(chess.Move, chess.Move.from_uci(cast(c.Move, best_move).uci()))

    if best_wdl == 2:
        return best_move, 1
    elif best_wdl == -2:
        return best_move, 0
    else:
        return best_move, 0.5

def get_best_opening_move(board: chess.Board) -> chess.Move | None:
    try:
        chs_board = c.Board(board.fen())

        best_move = opening_book.weighted_choice(chs_board)
        chess_move = chess.Move.from_uci(best_move.move.uci())

        return chess_move
    except IndexError:
        return None

def store_tt(b: chess.Board, b_fen: str, score: float, depth: int, flag: int, move: chess.Move | None) -> None:
    global tt
    tt[b_fen] = TTEntry(
        score=score,
        depth=depth,
        flag=flag,
        move=move,
        age=b.fullmove_number
    )

def clean_tt(b: chess.Board) -> None:
    expiry_date = b.fullmove_number - 2
    expired = set()
    for b_fen, entry in tt.items():
        if entry.age <= expiry_date:
            expired.add(b_fen)
    for b_fen in expired:
        del tt[b_fen]

def search(b: chess.Board, alpha: float, beta: float, depth: int, ply: int, is_pv: bool, end: float) -> float:
    global nodes
    nodes += 1

    if nodes % 64 == 0:
        if perf_counter() > end:
            raise TimeoutError

    b_fen = b.fen()
    entry = tt.get(b_fen, None)
    if entry is not None:
        if entry.depth >= depth:
            score = entry.score
            if score > INF_THRESHHOLD:
                score = INF - ply
            elif score < -INF_THRESHHOLD:
                score = -INF + ply

            if entry.flag == EXACT:
                return score
            elif entry.flag == UPPER:
                beta = min(beta, score)
            elif entry.flag == LOWER:
                alpha = max(alpha, score)

            if alpha >= beta:
                return score

    if USE_SYZYGY:
        if len(b[None]) >= 59:
            c_board = c.Board(b_fen)
            wdl = tablebase.probe_wdl(c_board)
            if -1 <= wdl <= 1:
                return wdl
            dtz = tablebase.probe_dtz(c_board)
            if dtz + b.halfmove_clock >= 100:
                score = wdl
                return wdl
            elif wdl > 0:
                score = TABLEBASE_INF - dtz
            else:
                score = -TABLEBASE_INF - dtz

            store_tt(b, b_fen, score, INF, EXACT, None)

            return score

    if b in chess.DRAW:
        return 0.0
    elif b in chess.CHECKMATE:
        return -INF + ply

    if depth <= 0:
        return nnue.nnue_evaluate_fen(b.fen())

    original_alpha = alpha

    best_score = -INF
    best_move = None
    for n, move in enumerate(b.legal_moves()):
        b.apply(move)
        if n == 0:
            score = -search(b, -beta, -alpha, depth - 1, ply + 1, True, end)
        else:
            score = -search(b, -alpha - 1, -alpha, depth - 1, ply + 1, False, end)
            if score > alpha:
                score = -search(b, -beta, -alpha, depth - 1, ply + 1, True, end)
        b.undo()

        if score > best_score:
            best_score = score
            best_move = move

        if score > alpha:
            alpha = score

        if alpha >= beta:
            break

    if best_score <= original_alpha:
        flag = UPPER
    elif best_score >= beta:
        flag = LOWER
    else:
        flag = EXACT

    tt_score = best_score
    if tt_score > INF_THRESHHOLD:
        tt_score = INF - ply
    elif tt_score < -INF_THRESHHOLD:
        tt_score = -INF_THRESHHOLD - ply

    store_tt(b, b_fen, tt_score, depth, flag, best_move)

    return best_score

def get_best_move(board: chess.Board, time_limit: float = TIME_LIMIT, max_depth: int = MAX_DEPTH) -> tuple[chess.Move, float]:
    global nodes
    nodes = 0

    if USE_OPENING:
        opening_move = get_best_opening_move(board)
        if opening_move is not None:
            return opening_move, nnue.nnue_evaluate_fen(board.fen())

    if USE_SYZYGY:
        if len(board[None]) >= 59:
            return get_best_tablebase_move(board)

    end = perf_counter() + time_limit

    best_move = None
    best_alpha = -INF
    cur_best_move = None
    alpha = -INF

    clean_tt(board)

    try:
        for depth in range(1, max_depth + 1):
            cur_best_move = None
            alpha = -INF

            board_copy = board.copy()

            legal_moves = board_copy.legal_moves()

            if best_move is not None and best_move in legal_moves:
                legal_moves.remove(best_move)
                legal_moves.insert(0, best_move)

            for n, move in enumerate(legal_moves):
                board_copy.apply(move)
                if n == 0:
                    score = -search(board_copy, -INF, INF, depth - 1, 1, True, end)
                else:
                    score = -search(board_copy, -alpha - 1, -alpha, depth - 1, 1, False, end)
                    if score > alpha:
                        score = -search(board_copy, -INF, -alpha, depth - 1, 1, True, end)
                board_copy.undo()

                if score > alpha:
                    alpha = score
                    cur_best_move = move

            best_move = cur_best_move
            best_alpha = alpha

            store_tt(board, board.fen(), best_alpha, depth, EXACT, best_move)

            print(f"Depth: {depth}", end='\r')
            
    except TimeoutError:
        pass

    assert best_move

    return best_move, best_alpha

def main() -> None:
    board = chess.Board()
    print(board.pretty())
    if BOT_STARTS:
        move, evaluation = get_best_move(board)
        print(f"Bot selected: {move} (evaluation: {evaluation:.4f})")
        print(f"Nodes: {nodes}")
        board.apply(move)
        print(board.pretty())
    while not (board in chess.DRAW or board in chess.CHECKMATE):
        if not SELF_PLAY:
            move = get_user_move(board)
            board.apply(move)
            print(board.pretty())
            if board in chess.DRAW or board in chess.CHECKMATE:
                return
        move, evaluation = get_best_move(board)
        print(f"Bot selected: {move} (evaluation: {evaluation:.4f})")
        print(f"Nodes: {nodes}")
        board.apply(move)
        print(board.pretty())

if __name__ == "__main__":
    main()
