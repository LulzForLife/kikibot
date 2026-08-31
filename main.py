from __future__ import annotations

import bulletchess as chess
import chess as c
import chess.polyglot as polyglot
import chess.syzygy as syzygy

import nnue

from time import perf_counter
from dataclasses import dataclass

from typing import cast, Literal
from collections.abc import Generator

InputModeOption = Literal["UCI", "SAN"]

SELF_PLAY = False
BOT_STARTS = False
USE_OPENING = False
USE_SYZYGY = True
INPUT_MODE: InputModeOption = "UCI"

TIME_LIMIT = 10.0
MAX_DEPTH = 10

INF = 100000.0
INF_THRESHHOLD = 97500.0
TABLEBASE_INF = 95000.0
TABLEBASE_INF_THRESHHOLD = 90000.0
MAX_PLY = 64

EXACT = 0
UPPER = 1
LOWER = 2

nodes = 0
tt: dict[str, TTEntry] = {}

killer0: list[chess.Move | None] = [None for _ in range(MAX_PLY)]
killer1: list[chess.Move | None] = [None for _ in range(MAX_PLY)]
history_white: dict[chess.Move, int] = {}
history_black: dict[chess.Move, int] = {}
counter_moves: dict[chess.Move, chess.Move] = {}

tablebase = syzygy.Tablebase()
try:
    tablebase.add_directory("syzygy")
except FileNotFoundError:
    del tablebase
    USE_SYZYGY = False

opening_book = polyglot.open_reader("komodo.bin")

MMVLVA = {
    chess.KING: INF,
    chess.QUEEN: 900,
    chess.ROOK: 500,
    chess.BISHOP: 330,
    chess.KNIGHT: 300,
    chess.PAWN: 100
}

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
            move = None
            if INPUT_MODE == "SAN":
                move = chess.Move.from_san(input("Enter move (e.g. e4): "), b)
            else:
                move = chess.Move.from_uci(input("Enter move (e.g. e2e4): "))
            if move is None:
                raise ValueError
            if move not in legal_moves:
                raise ValueError
            break
        except ValueError:
            ...
    return move

def get_best_tablebase_move(b: chess.Board) -> tuple[chess.Move, float]:

    c_board = c.Board(b.fen())

    best_wdl = int(-INF)
    best_dtz = int(INF)
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

    assert best_move is not None
    best_chess_move = chess.Move.from_uci(best_move.uci())
    assert best_chess_move is not None

    if best_wdl == 2:
        return best_chess_move, TABLEBASE_INF
    elif best_wdl == -2:
        return best_chess_move, -TABLEBASE_INF
    else:
        return best_chess_move, 0

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

def store_killer(move: chess.Move, ply: int) -> None:
    global killer0, killer1

    if killer0[ply] != move:
        killer1[ply] = killer0[ply]
        killer0[ply] = move

def clear_killer() -> None:
    global killer0, killer1

    killer0 = [None for _ in range(MAX_PLY)]
    killer1 = [None for _ in range(MAX_PLY)]

def store_history(move: chess.Move, depth: int, turn: chess.Color) -> None:
    global history_white, history_black

    if turn is chess.WHITE:
        history_white[move] = history_white.get(move, 0) + (depth * depth)

def decay_history() -> None:
    global history_white, history_black

    history_white = {move: (score >> 1) for move, score in history_white.items()}
    history_black = {move: (score >> 1) for move, score in history_black.items()}

def order_moves(b: chess.Board, b_fen: str, ply: int, prev_move: chess.Move | None, captures_only: bool = False) -> Generator[chess.Move]:
    entry = tt.get(b_fen)
    if entry is not None:
        tt_move = entry.move
    else:
        tt_move = None

    if tt_move is not None:
        yield tt_move

    if b.turn is chess.WHITE:
        history = history_white
    else:
        history = history_black

    legal_moves = set(b.legal_moves())

    if tt_move is not None:
        legal_moves.remove(tt_move)

    k0 = killer0[ply]
    if k0 in legal_moves and k0 is not None:
        legal_moves.remove(k0)
    else:
        k0 = None
    k1 = killer1[ply]
    if k1 in legal_moves and k1 is not None:
        legal_moves.remove(k1)
    else:
        k1 = None

    promotion: dict[chess.PieceType, set[chess.Move]] = {
        chess.QUEEN: set(),
        chess.ROOK: set(),
        chess.BISHOP: set(),
        chess.KNIGHT: set()
    }
    winning: dict[chess.Move, float] = {}
    equal: set[chess.Move] = set()
    counters: set[chess.Move] = set()
    histories: dict[chess.Move, float] = {}
    losing: dict[chess.Move, float] = {}
    quiets: set[chess.Move] = set()

    for move in legal_moves:
        if move.is_promotion():
            promo = move.promotion
            assert promo
            promotion[promo].add(move)

    yield from promotion[chess.QUEEN]
    yield from promotion[chess.ROOK]
    yield from promotion[chess.BISHOP]
    yield from promotion[chess.KNIGHT]

    legal_moves.difference_update(promotion[chess.QUEEN], promotion[chess.ROOK], promotion[chess.BISHOP], promotion[chess.KNIGHT])

    for move in legal_moves:
        if move.is_capture(b):
            victim = b[move.destination]
            victim_type = chess.PAWN if victim is None else victim.piece_type
            attacker = b[move.origin]
            assert attacker is not None
            attacker_type = attacker.piece_type
            if victim_type == attacker_type:
                equal.add(move)
                continue
            victim_value = MMVLVA[victim_type]
            attacker_value = MMVLVA[attacker_type]
            diff = victim_value - attacker_value
            score = victim_value * 10 - attacker_value

            if diff > 0:
                winning[move] = score
            else:
                losing[move] = score

    for move, value in sorted(winning.items(), key=lambda t: t[1]):
        yield move
    yield from equal

    if not captures_only:
        if k0 is not None:
            yield k0
        if k1 is not None:
            yield k1

    legal_moves.difference_update(winning.keys(), equal)

    if not captures_only:
        if prev_move is not None:
            for move in legal_moves:
                if counter_moves.get(prev_move) == move:
                    counters.add(move)

            yield from counters

            legal_moves.difference_update(counters)

        for move in legal_moves:
            history_score = history.get(move)
            if history_score is None:
                quiets.add(move)
            else:
                histories[move] = history_score

        for move, value in sorted(histories.items(), key=lambda t: t[1]):
            yield move

    yield from losing

    if not captures_only:
        yield from quiets

def search(b: chess.Board, alpha: float, beta: float, depth: int, ply: int, is_pv: bool, previous_move: chess.Move, end: float) -> float:
    global nodes, counter_moves
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
                score = float(wdl)
            else:
                dtz = tablebase.probe_dtz(c_board)
                if dtz + b.halfmove_clock >= 100:
                    score = float(wdl)
                elif wdl > 0:
                    score = TABLEBASE_INF - dtz
                else:
                    score = -TABLEBASE_INF - dtz

            store_tt(b, b_fen, score, int(INF), EXACT, None)

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
    for n, move in enumerate(order_moves(b, b_fen, ply, previous_move)):
        b.apply(move)
        if n == 0:
            score = -search(b, -beta, -alpha, depth - 1, ply + 1, True, move, end)
        else:
            score = -search(b, -alpha - 1, -alpha, depth - 1, ply + 1, False, move, end)
            if score > alpha:
                score = -search(b, -beta, -alpha, depth - 1, ply + 1, True, move, end)
        b.undo()

        if score > best_score:
            best_score = score
            best_move = move

        if score > alpha:
            alpha = score

        if alpha >= beta:
            if not (move.is_capture(b) or move.is_promotion()):
                store_killer(move, ply)
                store_history(move, depth, b.turn)
                counter_moves[previous_move] = move
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

    if entry is not None:
        if flag == EXACT or entry.depth < depth:
            store_tt(b, b_fen, tt_score, depth, flag, best_move)
    else:
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

    clean_tt(board)
    clear_killer()
    history_white.clear()
    history_black.clear()

    try:
        for depth in range(1, max_depth + 1):
            cur_best_move = None
            alpha = -INF

            board_copy = board.copy()

            for n, move in enumerate(order_moves(board, board.fen(), 0, None)):
                board_copy.apply(move)
                if n == 0:
                    score = -search(board_copy, -INF, INF, depth - 1, 1, True, move, end)
                else:
                    score = -search(board_copy, -alpha - 1, -alpha, depth - 1, 1, False, move, end)
                    if score > alpha:
                        score = -search(board_copy, -INF, -alpha, depth - 1, 1, True, move, end)
                board_copy.undo()

                if score > alpha:
                    alpha = score
                    cur_best_move = move

            best_move = cur_best_move
            best_alpha = alpha

            store_tt(board, board.fen(), best_alpha, depth, EXACT, best_move)
            decay_history()

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
