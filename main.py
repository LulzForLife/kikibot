from __future__ import annotations

import bulletchess as chess
import bulletchess.utils as utils
import chess as c
import chess.polyglot as polyglot
import chess.syzygy as syzygy

import nnue

from time import perf_counter
from dataclasses import dataclass
from math import log, ceil

from typing import Callable, Literal
from collections.abc import Generator

InputModeOption = Literal["UCI", "SAN"]
PrintModeOption = Literal["DEPTH", "UCI", "NONE"]

SELF_PLAY = False
BOT_STARTS = False
USE_OPENING = False
USE_SYZYGY = True
PONDER = False
CLEAN_TT = False
INPUT_MODE: InputModeOption = "UCI"
PRINT_MODE: PrintModeOption = "DEPTH"

TIME_LIMIT = 10.0
MAX_DEPTH = 63

INF = 100000.0
INF_THRESHHOLD = 97500.0
TABLEBASE_INF = 95000.0
TABLEBASE_INF_THRESHHOLD = 90000.0
MAX_PLY = 64

EXACT = 0
UPPER = 1
LOWER = 2

nodes = 0
END = 0.0

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

ln: Callable[..., float] = lambda n: log(n) if n > 0 else 0.0

LMR: list[list[int]] = [
    [
        1 + int((ln(depth) * ln(move_index)) // 2)
        for move_index in range(218)
    ]
    for depth in range(MAX_PLY)
]

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

def get_best_syzygy_move(b: chess.Board) -> chess.Move:

    b_copy = b.copy()
    while b_copy.history:
        b_copy.undo()
    c_board = c.Board(b_copy.fen())
    for move in b.history:
        c_board.push(c.Move.from_uci(move.uci()))

    target_wdl = -tablebase.probe_wdl(c_board)
    target_dtz = abs(tablebase.probe_dtz(c_board)) - 1
    print(f"target_wdl={target_wdl},target_dtz={target_dtz}")

    for move in c_board.legal_moves:
        is_zeroing = c_board.is_zeroing(move)
        c_board.push(move)

        outcome = c_board.outcome(claim_draw=True)
        if outcome is not None:
            if outcome.winner is None:
                if abs(target_wdl) != 2:
                    chess_move = chess.Move.from_uci(move.uci())
                    assert chess_move
                    return chess_move
                c_board.pop()
                continue
            chess_move = chess.Move.from_uci(move.uci())
            assert chess_move
            return chess_move

        wdl = tablebase.probe_wdl(c_board)
        dtz = abs(tablebase.probe_dtz(c_board))
        c_board.pop()

        if wdl == target_wdl and (dtz == target_dtz or (target_dtz == 0 and is_zeroing)):
            chess_move = chess.Move.from_uci(move.uci())
            assert chess_move
            return chess_move

    move = utils.random_legal_move(b)
    if move is None:
        raise ValueError
    return move

def get_best_opening_move(board: chess.Board) -> chess.Move | None:
    try:
        chs_board = c.Board(board.fen())

        best_move = opening_book.weighted_choice(chs_board)
        chess_move = chess.Move.from_uci(best_move.move.uci())

        return chess_move
    except IndexError:
        return None

def get_pv(b: chess.Board, move: chess.Move) -> str:
    b_copy = b.copy()
    b_copy.apply(move)
    pv = move.uci()

    while True:
        b_fen = b_copy.fen()
        entry = tt.get(b_fen)
        if entry is None:
            break

        pv_move = entry.move
        if pv_move is None:
            break

        pv += f" {pv_move.uci()}"
        b_copy.apply(pv_move)

    return pv

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

def syzygy_probe(b: chess.Board, b_fen: str) -> float | None:
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

        return score
    else:
        return None

def order_moves(b: chess.Board, b_fen: str, ply: int, prev_move: chess.Move | None, captures_only: bool = False) -> Generator[chess.Move]:
    entry = tt.get(b_fen)
    if entry is not None:
        tt_move = entry.move
    else:
        tt_move = None

    if tt_move is not None:
        yield tt_move

    legal_moves = set(b.legal_moves())
    if tt_move is not None:
        legal_moves.remove(tt_move)

    if not legal_moves:
        return
    elif len(legal_moves) == 1:
        yield legal_moves.pop()
        return

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

        if b.turn is chess.WHITE:
            history = history_white
        else:
            history = history_black

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

def quiesce(b: chess.Board, alpha: float, beta: float, ply: int, previous_move: chess.Move | None) -> float:
    global nodes, counter_moves
    nodes += 1

    if nodes % 64 == 0:
        if perf_counter() > END:
            raise TimeoutError

    b_fen = b.fen()
    entry = tt.get(b_fen)
    if entry is not None:
        if entry.depth >= 0:
            score = entry.score
            if score > INF_THRESHHOLD:
                score += ply
            elif score < -INF_THRESHHOLD:
                score -= ply

            if entry.flag == EXACT:
                return score
            elif entry.flag == UPPER:
                beta = min(beta, score)
            elif entry.flag == LOWER:
                alpha = max(alpha, score)

            if alpha >= beta:
                return alpha

    if USE_SYZYGY:
        syzygy_score = syzygy_probe(b, b_fen)
        if syzygy_score is not None:
            store_tt(b, b_fen, syzygy_score, int(INF), EXACT, None)
            return syzygy_score

    if b in chess.DRAW:
        return 0.0
    elif b in chess.CHECKMATE:
        return -INF + ply

    original_alpha = alpha

    stand_pat = nnue.nnue_evaluate_fen(b_fen)
    if stand_pat > alpha:
        alpha = stand_pat
    if alpha >= beta:
        store_tt(b, b_fen, alpha, 0, LOWER, None)
        return alpha

    best_score = stand_pat
    best_move = None
    for move in order_moves(b, b_fen, ply, previous_move, True):
        b.apply(move)
        score = -quiesce(b, -beta, -alpha, ply + 1, move)
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
        tt_score -= ply
    elif tt_score < -INF_THRESHHOLD:
        tt_score += ply

    if entry is not None:
        if flag == EXACT or entry.depth < 0:
            store_tt(b, b_fen, tt_score, 0, flag, best_move)
    else:
        store_tt(b, b_fen, tt_score, 0, flag, best_move)

    return best_score

def search(b: chess.Board, alpha: float, beta: float, depth: int, ply: int, is_pv: bool, is_null: bool, previous_move: chess.Move | None) -> float:
    global nodes, counter_moves
    nodes += 1

    if nodes % 64 == 0:
        if perf_counter() > END:
            raise TimeoutError

    mating_value = INF - ply
    if mating_value < beta:
        beta = mating_value
        if alpha >= beta:
            return mating_value

    mating_value = -INF + ply
    if mating_value > alpha:
        alpha = mating_value
        if alpha >= beta:
            return mating_value

    b_fen = b.fen()
    entry = tt.get(b_fen)
    if entry is not None:
        if entry.depth >= depth:
            score = entry.score
            if score > INF_THRESHHOLD:
                score += ply
            elif score < -INF_THRESHHOLD:
                score -= ply

            if entry.flag == EXACT:
                return score
            elif entry.flag == UPPER:
                beta = min(beta, score)
            elif entry.flag == LOWER:
                alpha = max(alpha, score)

            if alpha >= beta:
                return alpha

    if USE_SYZYGY:
        syzygy_score = syzygy_probe(b, b_fen)
        if syzygy_score is not None:
            store_tt(b, b_fen, syzygy_score, int(INF), EXACT, None)
            return syzygy_score

    in_check = b in chess.CHECK

    if b in chess.DRAW:
        return 0.0
    elif in_check and b in chess.CHECKMATE:
        return -INF + ply

    if depth <= 0:
        return quiesce(b, alpha, beta, ply, previous_move)

    static_eval = None
    is_pruning = (
        not in_check and
        not is_pv and
        max(abs(beta), abs(alpha)) < TABLEBASE_INF_THRESHHOLD
    )

    if depth <= 4 and is_pruning:
        static_eval = nnue.nnue_evaluate_fen(b_fen)

        margin = depth * 80

        if static_eval - margin >= beta:
            return static_eval - margin

    if depth <= 3 and is_pruning:
        if static_eval is None:
            static_eval = nnue.nnue_evaluate_fen(b_fen)

        margin = 200 + (depth * 100)

        if static_eval + margin <= alpha:
            v = quiesce(b, alpha, alpha + 1, ply, previous_move)

            if v + margin <= alpha:
                return v

    only_pawns = not (
        b[(b.turn, chess.QUEEN)] or
        b[(b.turn, chess.ROOK)] or
        b[(b.turn, chess.BISHOP)] or
        b[(b.turn, chess.KNIGHT)]
    )

    if depth >= 3 and is_pruning and not is_null and not only_pawns:
        if static_eval is None:
            static_eval = nnue.nnue_evaluate_fen(b_fen)

        if static_eval >= beta:
            b.apply(None)

            r = 3 if depth > 6 else 2
            score = -search(b, -beta, -beta + 1, depth - r, ply + 1, False, True, None)

            b.undo()

            if score >= beta:
                if score >= TABLEBASE_INF_THRESHHOLD:
                    return beta
                return score

    futility_pruning = False
    if depth <= 2 and is_pruning:
        if static_eval is None:
            static_eval = nnue.nnue_evaluate_fen(b_fen)

        if depth == 1:
            margin = 150
        elif depth == 2:
            margin = 300
        else:
            margin = 0

        if static_eval + margin <= alpha:
            futility_pruning = True

    original_alpha = alpha

    best_score = -INF
    best_move = None
    for n, move in enumerate(order_moves(b, b_fen, ply, previous_move)):
        is_quiet = not (move.is_capture(b) or move.is_promotion())

        if futility_pruning and not (move == killer0[ply] or move == killer1[ply] or n == 0):
            if is_quiet:
                continue

        if depth < MAX_PLY:
            depth_reduction = LMR[depth][n]
        else:
            depth_reduction = 0

        b.apply(move)
        if n == 0 or not is_quiet:
            score = -search(b, -beta, -alpha, depth - 1, ply + 1, True, False, move)
        else:
            score = -search(b, -alpha - 1, -alpha, depth - 1 - depth_reduction, ply + 1, False, False, move)
            if score > alpha:
                score = -search(b, -beta, -alpha, depth - 1, ply + 1, True, False, move)
        b.undo()

        if score > best_score:
            best_score = score
            best_move = move

        if score > alpha:
            alpha = score

        if alpha >= beta:
            if is_quiet:
                store_killer(move, ply)
                store_history(move, depth, b.turn)
                if previous_move is not None:
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
        tt_score -= ply
    elif tt_score < -INF_THRESHHOLD:
        tt_score += ply

    if entry is not None:
        if flag == EXACT or entry.depth < depth:
            store_tt(b, b_fen, tt_score, depth, flag, best_move)
    else:
        store_tt(b, b_fen, tt_score, depth, flag, best_move)

    return best_score

def get_best_move(board: chess.Board, time_limit: float | None = None, max_depth: int | None = None, *, print_mode: PrintModeOption | None = None) -> tuple[chess.Move, float]:
    global nodes, history_white, history_black, END
    nodes = 0

    if time_limit is None:
        time_limit = TIME_LIMIT

    if max_depth is None:
        max_depth = MAX_DEPTH

    if print_mode is None:
        print_mode = PRINT_MODE

    start = perf_counter()
    if not PONDER:
        END = start + time_limit
    else:
        END = start + 86400

    if USE_OPENING:
        opening_move = get_best_opening_move(board)
        if opening_move is not None:
            score = nnue.nnue_evaluate_fen(board.fen())
            if print_mode == "UCI":
                elapsed = perf_counter() - start
                elapsed_ms = max(1, int(elapsed * 1000))
                print(f"info depth 0 score cp {score} nodes 0 nps 0 time {elapsed_ms} pv {opening_move.uci()}")
            return opening_move, nnue.nnue_evaluate_fen(board.fen())

    if USE_SYZYGY:
        if len(board[None]) >= 59:
            move = get_best_syzygy_move(board)
            score = 0
            if print_mode == "UCI":
                elapsed = perf_counter() - start
                elapsed_ms = max(1, int(elapsed * 1000))
                if abs(score) > INF_THRESHHOLD:
                    plies_to_mate = INF - abs(score)
                    moves_to_mate = ceil(plies_to_mate / 2)
                    score_str = f"mate {int(moves_to_mate) if score > 0 else -int(moves_to_mate)}"
                else:
                    score_str = f"cp {int(score)}"
                print(f"info depth 0 score {score_str} nodes 0 nps 0 time {elapsed_ms} pv {move.uci()}")
            return move, score

    best_move = None
    best_alpha = -INF

    if CLEAN_TT:
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
                    score = -search(board_copy, -INF, INF, depth - 1, 1, True, False, move)
                else:
                    score = -search(board_copy, -alpha - 1, -alpha, depth - 1, 1, False, False, move)
                    if score > alpha:
                        score = -search(board_copy, -INF, -alpha, depth - 1, 1, True, False, move)
                board_copy.undo()

                if score > alpha:
                    alpha = score
                    cur_best_move = move

            best_move = cur_best_move
            best_alpha = alpha

            store_tt(board, board.fen(), best_alpha, depth, EXACT, best_move)
            decay_history()

            elapsed = perf_counter() - start
            elapsed_ms = max(1, int(elapsed * 1000))
            nps = int(nodes / elapsed) if elapsed > 0 else 0

            if print_mode == "DEPTH":
                print(f"Depth: {depth} (nps {nps})", end='\r')
            elif print_mode == "UCI":
                if abs(best_alpha) > INF_THRESHHOLD:
                    plies_to_mate = INF - abs(best_alpha)
                    moves_to_mate = ceil(plies_to_mate / 2)
                    score_str = f"mate {int(moves_to_mate) if best_alpha > 0 else -int(moves_to_mate)}"
                else:
                    score_str = f"cp {int(best_alpha)}"

                if best_move is not None:
                    pv = get_pv(board, best_move)
                    print(f"info depth {depth} score {score_str} nodes {nodes} nps {nps} time {elapsed_ms} pv {pv}", flush=True)

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
