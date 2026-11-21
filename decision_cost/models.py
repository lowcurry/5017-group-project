"""Tiny CPU error-risk heads for low-data routing experiments."""
from time import perf_counter
import numpy as np

FEATURES = ('direction_task', 'peak_task', 'alert_task', 'citation_fraction',
            'has_formula_tag', 'has_dataset_tag', 'has_numeric_action')
MODELS = ('logistic', 'frozen_head', 'tuned_encoder')


def features(rule, claim, action):
    claim = claim or {}
    # Raw count only. Required/expected citations, replay scores and group IDs are hidden.
    return [float(rule == name) for name in ('share_direction', 'peak_follow_up_hour', 'concentration_alert')] + [
        min(len(set(claim.get('evidence_ids', []))), 26) / 26,
        float(bool(claim.get('formula_version'))), float(bool(claim.get('dataset_sha256'))),
        float(action is not None)]


def sigmoid(x):
    return 1 / (1 + np.exp(-np.clip(x, -40, 40)))


def fit(x, y, mode, seed):
    """Fit a tabular autoencoder on training inputs, then a regularized error head."""
    start = perf_counter()
    w = None
    b = None
    z = x
    if mode != 'logistic':
        rng = np.random.default_rng(seed)
        w = rng.normal(0, 0.25, (x.shape[1], 4))
        b = np.zeros(4)
        decoder = rng.normal(0, 0.25, (4, x.shape[1]))
        offset = np.zeros(x.shape[1])
        for _ in range(150):
            z = np.tanh(x @ w + b)
            residual = (z @ decoder + offset - x) / len(x)
            gradient = residual @ decoder.T * (1 - z*z)
            w -= 0.08 * (x.T @ gradient)
            b -= 0.08 * gradient.sum(axis=0)
            decoder -= 0.08 * (z.T @ residual)
            offset -= 0.08 * residual.sum(axis=0)
        z = np.tanh(x @ w + b)
    head = np.zeros(z.shape[1])
    # Beta(1,1) smoothing also permits fitting on a fold with no observed errors.
    prior = (sum(y) + 1) / (len(y) + 2)
    bias = np.log(prior / (1 - prior))
    for _ in range(150):
        if w is not None:
            z = np.tanh(x @ w + b)
        probabilities = sigmoid(z @ head + bias)
        error = (probabilities - y) / (len(y) + 2)
        if mode == 'tuned_encoder':
            gradient = error[:, None] * head * (1 - z*z)
            w -= 0.02 * (x.T @ gradient)
            b -= 0.02 * gradient.sum(axis=0)
        head -= 0.1 * (z.T @ error + 0.05 * head)
        bias -= 0.1 * (error.sum() + (2*sigmoid(bias) - 1)/(len(y)+2))
    return {'mode': mode, 'w': None if w is None else w.tolist(),
            'b': None if b is None else b.tolist(), 'head': head.tolist(), 'bias': float(bias)}, perf_counter()-start


def predictor(model):
    head, bias = np.array(model['head']), model['bias']
    if model['w'] is None:
        return lambda row: float(sigmoid(row @ head + bias))
    w, b = np.array(model['w']), np.array(model['b'])
    return lambda row: float(sigmoid(np.tanh(row @ w + b) @ head + bias))
