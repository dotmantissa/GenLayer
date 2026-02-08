# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import typing

class RepScore(gl.Contract):
    """
    Tracks validator reputation. 
    Deterministic logic: Decrements score on logged dissent.
    """
    
    # Storage: Validator Address -> Reputation Score (u256)
    # Default score is 100.
    scores: TreeMap[str, u256]

    def __init__(self):
        pass
        
    @gl.public.write
    def log_dissent(self, validator_addr: str) -> None:
        """
        Decrements the validator's score by 1.
        """
        # Retrieve current score (default to 100 if not present)
        current_score = 100
        if validator_addr in self.scores:
            current_score = int(self.scores[validator_addr])
            
        # Decrement (prevent underflow below 0)
        new_score = current_score - 1
        if new_score < 0:
            new_score = 0
            
        # Update State
        self.scores[validator_addr] = u256(new_score)
        
        return None

    @gl.public.view
    def get_score(self, validator_addr: str) -> int:
        if validator_addr in self.scores:
            return int(self.scores[validator_addr])
        return 100
