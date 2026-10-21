"""R03 quantitative shadow. Disabled by default and isolated from official flow."""
from .adapter import R03_QUANT_SHADOW_ENABLED_DEFAULT, run_shadow_fail_open, run_shadow_observer
from .health import shadow_health_check
from .inference import run_quant_shadow

__all__ = ["R03_QUANT_SHADOW_ENABLED_DEFAULT", "run_quant_shadow", "run_shadow_fail_open", "run_shadow_observer", "shadow_health_check"]
