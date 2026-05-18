from symbolic_core.four_d_inference import infer_chain

def inject_braid(cluster):
    results = []
    for symbol in cluster:
        result = infer_chain(symbol)
        results.append(result)
    return results
