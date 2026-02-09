# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class PegWatch(gl.Contract):
    """
    Monitors USDC price across multiple exchanges.
    Triggers an ALARM (True) if the price drops below $0.98.
    Uses a waterfall strategy to bypass bot protections.
    """
    
    # Stores the alarm status:
    # True = PEG BROKEN (< $0.98)
    # False = PEG SAFE (>= $0.98)
    is_peg_broken: bool
    last_checked_price: u256 # Stored as Scaled Integer (x10,000)

    def __init__(self):
        self.is_peg_broken = False
        self.last_checked_price = u256(10000) # Default $1.00

    @gl.public.write
    def check_peg_health(self) -> None:
        """
        Scrapes USDC price from 3 sources.
        Updates 'is_peg_broken' based on the consensus price.
        """
        
        # Strategy: Use DuckDuckGo to search specifically for the price on major sites.
        # This is often more reliable than hitting the exchange URLs directly.
        urls = [
            "https://html.duckduckgo.com/html?q=USDC+price+usd+site:coingecko.com",
            "https://html.duckduckgo.com/html?q=USDC+price+usd+site:coinbase.com",
            "https://html.duckduckgo.com/html?q=USDC+price+usd+site:kraken.com"
        ]

        def fetch_price_nondet() -> str:
            # Waterfall: Try sources until we get a valid response
            page_text = ""
            for url in urls:
                print(f"Checking peg via: {url}")
                try:
                    content = gl.nondet.web.render(url, mode="text")
                    if len(content) > 500: # Basic check for valid content
                        page_text = content
                        break
                except:
                    continue
            
            if not page_text:
                return json.dumps({"price": 1.00})

            task = f"""
            Act as a Financial Analyst.
            
            Task: Extract the current USDC (USD Coin) price in USD.
            
            Search Results:
            {page_text[:4000]}
            
            Instructions:
            1. Look for text like "1 USDC = $1.00" or "Price: $0.99".
            2. Ignore "volume" or "market cap" numbers.
            3. Return the price as a float.
            4. If the price is effectively $1.00 (e.g. 0.9999 or 1.0001), return 1.0.
            
            Respond using ONLY JSON:
            {{ "price": float }}
            """

            result_raw = gl.nondet.exec_prompt(task)
            try:
                cleaned = result_raw.replace("```json", "").replace("```", "").strip()
                return cleaned
            except:
                return json.dumps({"price": 1.00})

        # Consensus: Comparative (Float Match)
        comparison_criteria = """
        Compare 'price' floats.
        
        Logic:
        1. Parse val_a and val_b.
        2. If abs(val_a - val_b) < 0.005, they are EQUAL.
        3. Otherwise, they are DIFFERENT.
        """

        consensus_json = gl.eq_principle.prompt_comparative(
            fetch_price_nondet, 
            comparison_criteria
        )

        try:
            parsed = json.loads(consensus_json)
            price = float(parsed.get("price", 1.00))
            
            # Update Price Logic
            # 0.98 Threshold Check
            if price < 0.98:
                self.is_peg_broken = True
            else:
                self.is_peg_broken = False
                
            # Store price for visibility (x10,000 for 4 decimal places)
            # $0.9998 -> 9998
            self.last_checked_price = u256(int(price * 10000))
            
        except:
            pass # Keep previous state on error
        
        return None

    @gl.public.view
    def get_status(self) -> bool:
        """
        Returns True if the peg is broken (< $0.98).
        """
        return self.is_peg_broken

    @gl.public.view
    def get_latest_price(self) -> str:
        """
        Returns the last checked price as a string (e.g. "0.9998").
        """
        val = int(self.last_checked_price)
        return f"{val / 10000.0}"
