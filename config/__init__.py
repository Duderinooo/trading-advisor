"""Configuration for the active trading advisor.

Split into sections by concern (risk / execution / market / sectors / watchlists /
llm / flags). Callers keep using `import config` + `config.MAX_RISK_PER_TRADE_PERCENT`
— every public constant is re-exported here.
"""

from config.execution import *  # noqa: F401,F403
from config.flags import *  # noqa: F401,F403
from config.llm import *  # noqa: F401,F403
from config.market import *  # noqa: F401,F403
from config.risk import *  # noqa: F401,F403
from config.sectors import *  # noqa: F401,F403
from config.watchlists import *  # noqa: F401,F403
