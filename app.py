# app.py
import threading
import hashlib
import json
import time as time_module
from time import time
from flask import Flask, render_template, request, jsonify
from main import Blockchain, AttackSimulator, PerformanceBenchmark

app = Flask(__name__)

# ── Network: 4 independent nodes with the same genesis ──
NODES = {
    "node_0": Blockchain("node_0", difficulty=3),
    "node_1": Blockchain("node_1", difficulty=3),
    "node_2": Blockchain("node_2", difficulty=3),
    "node_3": Blockchain("node_3", difficulty=3),
}
PRIMARY = "node_0"


def primary() -> Blockchain:
    return NODES[PRIMARY]


# ─────────────────────────────────────────────────────────
# BASIC CHAIN OPERATIONS
# ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/transactions/new", methods=["POST"])
def new_transaction():
    v = request.get_json()
    if not all(k in v for k in ["sender", "recipient", "amount"]):
        return "Missing values", 400
    tx = primary().add_transaction(v["sender"], v["recipient"], v["amount"])
    return jsonify({"message": "Transaction added to mempool", "tx": tx}), 201


@app.route("/mine", methods=["GET"])
def mine():
    import time as t
    blk, elapsed = primary().mine()
    if not blk:
        return jsonify({"message": "Mempool is empty"}), 400
    return jsonify({
        "message": "New Block Mined",
        "index": blk.index,
        "transactions": blk.transactions,
        "nonce": blk.nonce,
        "previous_hash": blk.previous_hash,
        "hash": blk.hash,
        "mine_time_sec": round(elapsed, 4)
    }), 200


@app.route("/chain", methods=["GET"])
def full_chain():
    return jsonify({
        "node": PRIMARY,
        "chain": primary().chain_as_dicts(),
        "length": len(primary().chain)
    }), 200


# ─────────────────────────────────────────────────────────
# FEATURE 3 — CONSENSUS
# ─────────────────────────────────────────────────────────

@app.route("/nodes/status", methods=["GET"])
def nodes_status():
    """Returns chain length + last block hash for every node."""
    return jsonify({
        nid: {
            "length": len(bc.chain),
            "last_hash": bc.last_block.hash[:20] + "...",
            "difficulty": bc.difficulty
        }
        for nid, bc in NODES.items()
    }), 200


@app.route("/nodes/sync", methods=["POST"])
def sync_nodes():
    """
    Copies node_0's current chain to all other nodes so they share
    the exact same tip. After this, mining on any node extends the
    same chain — which is the correct setup for consensus demonstration.
    """
    source = primary()
    source_chain = source.chain_as_dicts()
    report = {}
    for nid, bc in NODES.items():
        if nid == PRIMARY:
            report[nid] = {"status": "source — unchanged", "length": len(bc.chain)}
            continue
        # Rebuild the chain from node_0's data
        from main import Block as _Block
        bc.chain = []
        for d in source_chain:
            b = _Block(d["index"], d["transactions"],
                       d["timestamp"], d["previous_hash"], d["nonce"])
            b.hash = d["hash"]
            bc.chain.append(b)
        bc.unconfirmed_transactions = []
        bc._save()
        report[nid] = {"status": "synced to node_0", "length": len(bc.chain)}
    return jsonify({"synced_to_length": len(source.chain), "nodes": report}), 200


@app.route("/nodes/mine/<node_id>", methods=["POST"])
def mine_on_node(node_id):
    """
    Mine N blocks on a specific node (to create genuine chain divergence).
    Body: { "blocks": 3, "sender": "X", "recipient": "Y" }
    """
    if node_id not in NODES:
        return jsonify({"error": f"Unknown node {node_id}"}), 404

    bc = NODES[node_id]
    body = request.get_json(silent=True) or {}
    n = int(body.get("blocks", 1))
    sender = body.get("sender", f"Miner_{node_id}")
    recipient = body.get("recipient", "Network")

    mined = []
    for i in range(n):
        bc.add_transaction(sender, recipient, i + 1)
        blk, elapsed = bc.mine()
        if blk:
            mined.append({
                "index": blk.index,
                "nonce": blk.nonce,
                "hash": blk.hash[:20] + "...",
                "mine_time_sec": round(elapsed, 4)
            })

    return jsonify({
        "node": node_id,
        "blocks_mined": len(mined),
        "new_chain_length": len(bc.chain),
        "blocks": mined
    }), 200


@app.route("/consensus/run", methods=["POST"])
def run_consensus():
    """
    Runs Nakamoto consensus on the primary node against all peers.
    Each peer's chain is fully validated block-by-block before adoption.
    Body: { "target_node": "node_0" }  (optional, default node_0)
    """
    body = request.get_json(silent=True) or {}
    target_id = body.get("target_node", PRIMARY)
    if target_id not in NODES:
        return jsonify({"error": f"Unknown node {target_id}"}), 404

    target_bc = NODES[target_id]
    peers = [
        (nid, bc.chain_as_dicts())
        for nid, bc in NODES.items()
        if nid != target_id
    ]

    report = target_bc.resolve_conflicts(peers)

    # After consensus, propagate the winning chain to ALL other nodes
    # so the entire network converges — not just the target node.
    # In a real network this is done by re-broadcasting the winning block.
    winning_chain = target_bc.chain_as_dicts()
    propagation_log = []
    for nid, bc in NODES.items():
        if nid == target_id:
            propagation_log.append({"node": nid, "status": "consensus runner — already up to date"})
            continue
        if len(bc.chain) == len(target_bc.chain) and bc.last_block.hash == target_bc.last_block.hash:
            propagation_log.append({"node": nid, "status": "already on winning chain — no update needed"})
            continue
        # Overwrite this node's chain with the winning chain
        from main import Block as _Block
        bc.chain = []
        for d in winning_chain:
            b = _Block(d["index"], d["transactions"],
                       d["timestamp"], d["previous_hash"], d["nonce"])
            b.hash = d["hash"]
            bc.chain.append(b)
        bc.unconfirmed_transactions = []
        bc._save()
        propagation_log.append({
            "node": nid,
            "status": f"updated to winning chain (length={len(bc.chain)})"
        })

    report["network_propagation"] = propagation_log
    report["all_nodes_length"] = {nid: len(bc.chain) for nid, bc in NODES.items()}
    return jsonify(report), 200


# ─────────────────────────────────────────────────────────
# FEATURE 4 — ATTACK SIMULATION
# ─────────────────────────────────────────────────────────

@app.route("/attack/51", methods=["POST"])
def attack_51():
    """
    Real fork-based 51% attack simulation.
    Body: { "attacker_hash_pct": 60, "fork_depth": 2 }
    """
    body = request.get_json(silent=True) or {}
    pct = int(body.get("attacker_hash_pct", 60))
    depth = int(body.get("fork_depth", 2))

    if not (1 <= pct <= 99):
        return jsonify({"error": "attacker_hash_pct must be 1–99"}), 400

    sim = AttackSimulator(primary())
    report = sim.simulate_51_percent_attack(
        attacker_hash_pct=pct,
        fork_depth=depth
    )
    return jsonify(report), 200


@app.route("/attack/invalid-blocks", methods=["GET"])
def attack_invalid_blocks():
    """
    Injects 4 categories of invalid blocks into a copy of the chain.
    All must be rejected.
    """
    sim = AttackSimulator(primary())
    report = sim.simulate_invalid_block_rejection()
    return jsonify(report), 200


# ─────────────────────────────────────────────────────────
# FEATURE 5 — PERFORMANCE ANALYSIS
# ─────────────────────────────────────────────────────────

@app.route("/performance/block-creation", methods=["POST"])
def perf_block_creation():
    """
    Body: { "difficulty": 3, "num_blocks": 5, "txs_per_block": 3 }
    """
    body = request.get_json(silent=True) or {}
    results = PerformanceBenchmark.benchmark_block_creation(
        difficulty=int(body.get("difficulty", 3)),
        num_blocks=int(body.get("num_blocks", 5)),
        txs_per_block=int(body.get("txs_per_block", 3))
    )
    times = [r["time_sec"] for r in results]
    return jsonify({
        "blocks": results,
        "avg_time_sec": round(sum(times) / len(times), 6),
        "min_time_sec": round(min(times), 6),
        "max_time_sec": round(max(times), 6),
    }), 200


@app.route("/performance/throughput", methods=["POST"])
def perf_throughput():
    """
    Body: { "difficulty": 3, "num_blocks": 5, "txs_per_block": 5 }
    """
    body = request.get_json(silent=True) or {}
    result = PerformanceBenchmark.benchmark_throughput(
        difficulty=int(body.get("difficulty", 3)),
        num_blocks=int(body.get("num_blocks", 5)),
        txs_per_block=int(body.get("txs_per_block", 5))
    )
    return jsonify(result), 200


@app.route("/performance/network-delay", methods=["POST"])
def perf_network_delay():
    """
    Body: { "num_nodes": 5, "difficulty": 3 }
    """
    body = request.get_json(silent=True) or {}
    result = PerformanceBenchmark.benchmark_network_delay(
        num_nodes=int(body.get("num_nodes", 5)),
        difficulty=int(body.get("difficulty", 3))
    )
    return jsonify(result), 200


@app.route("/performance/scalability", methods=["POST"])
def perf_scalability():
    """
    Body: { "max_nodes": 8, "difficulty": 3, "txs_per_block": 3 }
    """
    body = request.get_json(silent=True) or {}
    results = PerformanceBenchmark.benchmark_scalability(
        max_nodes=int(body.get("max_nodes", 8)),
        difficulty=int(body.get("difficulty", 3)),
        txs_per_block=int(body.get("txs_per_block", 3))
    )
    conf_times = [r["total_confirmation_sec"] for r in results]
    return jsonify({
        "scalability": results,
        "avg_confirmation_sec": round(sum(conf_times) / len(conf_times), 6),
    }), 200


# ─────────────────────────────────────────────────────────
# PROOF-OF-WORK — NODE RACE + STEP VERIFIER
# ─────────────────────────────────────────────────────────

@app.route("/mine/race", methods=["POST"])
def mine_race():
    """
    All nodes race to mine the same block simultaneously using threads.
    The first node to find a valid nonce wins, broadcasts its block to
    all other nodes, which then validate and accept it via add_block().

    Body: { "sender": "Alice", "recipient": "Bob", "amount": 50 }
    Each node mines independently; threading simulates parallelism.
    """
    body = request.get_json(silent=True) or {}
    sender    = body.get("sender", "RaceSender")
    recipient = body.get("recipient", "RaceRecipient")
    amount    = float(body.get("amount", 10))

    # Add the same pending transaction to every node's mempool
    for bc in NODES.values():
        bc.add_transaction(sender, recipient, amount)

    # Shared winner state
    winner_lock   = threading.Lock()
    winner_result = {}          # filled by first thread to finish
    node_results  = {}          # every node's individual result
    finish_event  = threading.Event()

    def node_mine(node_id, bc):
        """Each node builds and mines its own candidate block."""
        import time as _t

        # Build candidate (from last block of this node's chain)
        from main import Block
        candidate = Block(
            index=bc.last_block.index + 1,
            transactions=bc.unconfirmed_transactions[:],
            timestamp=time(),
            previous_hash=bc.last_block.hash
        )

        t0 = _t.perf_counter()
        # Real PoW: iterate nonce until hash meets difficulty
        nonce = 0
        h = candidate.compute_hash()
        prefix = "0" * bc.difficulty
        while not h.startswith(prefix):
            # If another node already won, stop immediately
            if finish_event.is_set():
                node_results[node_id] = {
                    "status": "stopped — another node won first",
                    "nonces_tried": nonce,
                    "time_sec": round(_t.perf_counter() - t0, 4)
                }
                return
            nonce += 1
            candidate.nonce = nonce
            h = candidate.compute_hash()

        elapsed = round(_t.perf_counter() - t0, 4)

        with winner_lock:
            if not winner_result:
                # This node won — record and signal others to stop
                finish_event.set()
                candidate.hash = h
                bc.chain.append(candidate)
                bc.unconfirmed_transactions = []
                bc._save()

                winner_result.update({
                    "winner_node": node_id,
                    "block_index": candidate.index,
                    "nonce": nonce,
                    "hash": h,
                    "previous_hash": candidate.previous_hash,
                    "difficulty": bc.difficulty,
                    "leading_zeros_required": prefix,
                    "hash_starts_with": h[:bc.difficulty],
                    "pow_satisfied": h.startswith(prefix),
                    "mine_time_sec": elapsed,
                    "transactions": candidate.transactions,
                })

        node_results[node_id] = {
            "status": "WON" if not finish_event.is_set() or winner_result.get("winner_node") == node_id else "lost",
            "nonces_tried": nonce,
            "time_sec": elapsed,
            "hash": h[:24] + "..."
        }

    threads = [
        threading.Thread(target=node_mine, args=(nid, bc))
        for nid, bc in NODES.items()
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    if not winner_result:
        return jsonify({"error": "No node finished mining"}), 500

    # ── Broadcast: winner's block is sent to all other nodes ──
    broadcast_log = []
    winner_nid = winner_result["winner_node"]
    winning_bc  = NODES[winner_nid]
    winner_block_dict = winning_bc.chain[-1].to_dict()

    for nid, bc in NODES.items():
        if nid == winner_nid:
            broadcast_log.append({
                "node": nid, "role": "winner/broadcaster",
                "action": "already has the block"
            })
            continue
        # Clear the loser's mempool (block is now confirmed)
        bc.unconfirmed_transactions = []
        # Validate and adopt the winner's block
        ok, reason = bc.add_block(winner_block_dict)
        broadcast_log.append({
            "node": nid,
            "role": "receiver",
            "accepted": ok,
            "reason": reason
        })

    return jsonify({
        "winner": winner_result,
        "node_race_results": node_results,
        "broadcast_log": broadcast_log
    }), 200


@app.route("/pow/verify", methods=["POST"])
def pow_verify():
    """
    Step-by-step SHA-256 PoW verification for any block.
    Given block fields, re-runs compute_hash() and checks difficulty.

    Body: { "index": 1, "transactions": [...], "timestamp": ...,
            "previous_hash": "...", "nonce": 12345 }

    Returns every intermediate step so the student can see exactly
    how SHA-256 + nonce produces a valid hash.
    """
    body = request.get_json()
    required = ["index", "transactions", "timestamp", "previous_hash", "nonce"]
    if not all(k in body for k in required):
        return jsonify({"error": f"Missing fields. Required: {required}"}), 400

    difficulty = int(body.get("difficulty", primary().difficulty))
    prefix = "0" * difficulty

    # Step 1: Assemble the block dict that gets hashed
    block_dict = {
        "index":         body["index"],
        "transactions":  body["transactions"],
        "timestamp":     body["timestamp"],
        "previous_hash": body["previous_hash"],
        "nonce":         body["nonce"]
    }

    # Step 2: Serialize to JSON (sort_keys, same as compute_hash)
    serialized = json.dumps(block_dict, sort_keys=True)

    # Step 3: UTF-8 encode
    encoded = serialized.encode("utf-8")

    # Step 4: SHA-256
    computed_hash = hashlib.sha256(encoded).hexdigest()

    # Step 5: Check difficulty
    pow_valid = computed_hash.startswith(prefix)

    # Step 6: Check if stored hash matches (if provided)
    stored_hash    = body.get("hash", None)
    hash_matches   = (computed_hash == stored_hash) if stored_hash else None

    # Bonus: show what nonce-1 would have produced (to prove the loop was needed)
    if body["nonce"] > 0:
        prev_dict = dict(block_dict); prev_dict["nonce"] = body["nonce"] - 1
        prev_hash = hashlib.sha256(json.dumps(prev_dict, sort_keys=True).encode()).hexdigest()
        prev_valid = prev_hash.startswith(prefix)
    else:
        prev_hash, prev_valid = None, None

    return jsonify({
        "steps": {
            "1_block_fields":          block_dict,
            "2_json_serialized":       serialized,
            "3_utf8_hex_length_bytes": len(encoded),
            "4_sha256_hash":           computed_hash,
            "5_difficulty_required":   difficulty,
            "5_prefix_needed":         prefix,
            "5_hash_prefix_actual":    computed_hash[:difficulty],
            "5_pow_satisfied":         pow_valid,
            "6_stored_hash_matches":   hash_matches,
        },
        "nonce_minus_1_check": {
            "nonce":   body["nonce"] - 1 if body["nonce"] > 0 else None,
            "hash":    prev_hash,
            "valid":   prev_valid,
            "proves":  "previous nonce did NOT satisfy PoW — this nonce was necessary"
        },
        "verdict": "VALID PROOF-OF-WORK" if pow_valid else "INVALID — hash does not meet difficulty"
    }), 200


if __name__ == "__main__":
    app.run(debug=True, port=5000)