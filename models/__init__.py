# -*- coding: utf-8 -*-
# Order matters: the mixin must be loaded before the models that inherit it.
from . import maintenance_mixin
from . import maintenance_asset
from . import maintenance_part
from . import maintenance_plan
from . import maintenance_request
from . import demo_helper
