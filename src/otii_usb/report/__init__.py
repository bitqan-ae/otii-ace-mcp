# report/__init__.py: measurement plots
#
# Copyright (c) 2026 Milosch Meriac <meriac@bitqan.ae>
#    developed at Bitqan Systems Design (bitqan.ae)
#         From Idea to Manufacturable Product
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0
#
# PlotSink collects a recording for plotting; render_png draws it.

"""Reports: measurement plots."""
from .plot import DEFAULT_FOOTER, DEFAULT_POINTS, PlotInfo, PlotSink, render_png

__all__ = ["DEFAULT_FOOTER", "DEFAULT_POINTS", "PlotInfo", "PlotSink", "render_png"]
