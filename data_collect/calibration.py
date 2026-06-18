"""Calibration read/write API for Pixhawk gravity offset and Witmotion VZ scale."""

import os
from data_collect import state


# ---------------------------------------------------------------------------
# Pixhawk gravity offset
# ---------------------------------------------------------------------------

def get_pixhawk_offset() -> float:
    """Return the currently active Pixhawk gravity offset (mG)."""
    return state._actual_gravity_offset if hasattr(state, '_actual_gravity_offset') else state._gravity_offset_base


def get_pixhawk_base() -> float:
    """Return the on-disk base calibration value (mG)."""
    return state._gravity_offset_base


def set_pixhawk_offset(new_offset: float) -> None:
    """Apply a new gravity offset to the running reader immediately."""
    state._actual_gravity_offset = float(new_offset)


def save_pixhawk_offset(new_offset: float) -> str:
    """Persist a new gravity offset to the calibration file and apply it."""
    set_pixhawk_offset(new_offset)
    state._gravity_offset_base = float(new_offset)
    calib_dir = os.path.dirname(state.CALIB_FILE)
    if calib_dir:
        os.makedirs(calib_dir, exist_ok=True)
    with open(state.CALIB_FILE, 'w') as fh:
        fh.write(f'{new_offset:.6f}\n')
    return state.CALIB_FILE


def reset_pixhawk_offset() -> float:
    """Reset the active offset back to the on-disk base value."""
    base = state._gravity_offset_base
    state._actual_gravity_offset = base
    return base


# ---------------------------------------------------------------------------
# Witmotion VZ scale
# ---------------------------------------------------------------------------

def get_witmotion_scale() -> float:
    """Return the currently active Witmotion VZ scale multiplier."""
    return state.WITMOTION_VZ_SCALE


def get_witmotion_base_scale() -> float:
    """Return the on-disk base scale value."""
    return state._witmotion_vz_scale_base


def set_witmotion_scale(new_scale: float) -> None:
    """Apply a new VZ scale multiplier to the running reader immediately."""
    state.WITMOTION_VZ_SCALE = float(new_scale)


def save_witmotion_scale(new_scale: float) -> str:
    """Persist a new VZ scale to the calibration file and apply it."""
    set_witmotion_scale(new_scale)
    state._witmotion_vz_scale_base = float(new_scale)
    calib_dir = os.path.dirname(state.WITMOTION_CALIB_FILE)
    if calib_dir:
        os.makedirs(calib_dir, exist_ok=True)
    with open(state.WITMOTION_CALIB_FILE, 'w') as fh:
        fh.write(f'{new_scale:.6f}\n')
    return state.WITMOTION_CALIB_FILE


def reset_witmotion_scale() -> float:
    """Reset the active scale back to the on-disk base value."""
    base = state._witmotion_vz_scale_base
    state.WITMOTION_VZ_SCALE = base
    return base
