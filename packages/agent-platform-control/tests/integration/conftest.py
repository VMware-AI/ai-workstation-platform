"""Re-export smoke fixtures so pytest discovery picks them up without imports
in every test module (avoids ruff F811 noise on fixture-as-arg).
"""

from .lib_smoke import smoke_client, smoke_seed  # noqa: F401
