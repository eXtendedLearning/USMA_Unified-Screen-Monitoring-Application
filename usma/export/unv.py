import re
import logging
from datetime import datetime
from typing import Dict, Optional

import numpy as np

from usma.models import APP_VERSION, FRFAnalysisResult, FrameAnalysisResult

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# UNV EXPORTER
# ═══════════════════════════════════════════════════════════════
class UNVExporter:


    def export(self, frf_result: FRFAnalysisResult, frame_result: FrameAnalysisResult, 
                      frf_name: str, base_filename: str):
        def parse_point(point_str: Optional[str]) -> int: 
            if not point_str: return 1
            digits = re.sub(r'\D', '', point_str)
            return int(digits) if digits else 1

        def parse_dof(dir_str: Optional[str]) -> int: 
            if not dir_str: return 3
            ds = dir_str.upper()
            if ds.endswith('X'): return 1
            if ds.endswith('Y'): return 2
            return 3

        try:
            filename = f"signal_logs/{base_filename}.unv"
            region = frame_result.active_regions[frf_name]
            points = frame_result.points_info

            phys = frf_result.signal_physical
            if phys is None:
                return
            num_points = len(phys)

            if num_points < 2:
                logger.warning(f"Skipping UNV log for {frf_name}: insufficient data points ({num_points})")
                return

            start_freq = region.x_axis_min
            freq_step = (region.x_axis_max - region.x_axis_min) / (num_points - 1) if num_points > 1 else 0

            resp_node = parse_point(points.response_point)
            resp_dof = parse_dof(points.response_dir)
            ref_node = parse_point(points.hammer_point)
            ref_dof = parse_dof(points.hammer_dir)
            timestamp = datetime.now().strftime('%d-%b-%y %H:%M:%S')

            with open(filename, 'w') as f:
                f.write(f"    -1{' ' * 74}\n")
                f.write(f"    58{' ' * 74}\n")

                id_line1 = f"FRF for {points.response_point}:{points.response_dir}/{points.hammer_point}:{points.hammer_dir}"
                f.write(f"{id_line1[:80]:<80}\n")

                id_line2 = f"USMA v{APP_VERSION} - Screen Reconstruction"
                f.write(f"{id_line2[:80]:<80}\n")

                f.write(f"{timestamp:<80}\n")

                id_line4 = f"Reconstructed from {points.run}, region \"{frf_name}\""
                f.write(f"{id_line4[:80]:<80}\n")

                dir_char = {1: 'X', 2: 'Y', 3: 'Z'}.get(resp_dof, 'Z')
                id_line5 = f"FRF\\\\{points.response_point}:+{dir_char}"
                f.write(f"{id_line5[:80]:<80}\n")

                func_type = 4
                func_id = 0
                version = 0
                load_case = 0
                resp_node_name = "C"
                ref_node_name = "C"

                dof_line = (
                    f"{func_type:5d}"
                    f"{func_id:10d}"
                    f"{version:5d}"
                    f"{load_case:10d}"
                    f"{resp_node_name:10s}"
                    f"{resp_node:10d}"
                    f"{resp_dof:5d}"
                    f"{ref_node_name:10s}"
                    f"{ref_node:10d}"
                    f"{ref_dof:5d}\n"
                )
                f.write(dof_line)

                data_type = 2
                spacing = 1
                z_value = 0.0

                f.write(
                    f"{data_type:10d}"
                    f"{num_points:10d}"
                    f"{spacing:10d}"
                    f"{start_freq:13.5E}"
                    f"{freq_step:13.5E}"
                    f"{z_value:13.5E}\n"
                )

                f.write(f"{18:10d}{0:5d}{0:5d}{0:5d}{'X-axis':20s}{'Hz':20s}\n")
                f.write(f"{12:10d}{0:5d}{0:5d}{0:5d}{'Y-axis':20s}{region.y_axis_unit:20s}\n")
                f.write(f"{13:10d}{0:5d}{0:5d}{0:5d}{'Z-axis':20s}{'NONE':20s}\n")
                f.write(f"{0:10d}{0:5d}{0:5d}{0:5d}{'NONE':20s}{'NONE':20s}\n")

                for val in phys:
                    real_part = val
                    imag_part = 0.0
                    f.write(f"  {real_part:13.6E}  {imag_part:13.6E}\n")

                f.write("    -1\n")

            logger.info(f"UNV file saved: {filename}")

        except Exception as e: 
            logger.error(f"Failed to save .unv file for {frf_name}: {e}")

# --- 3. CORE LOGIC: THE SCREEN MONITOR ENGINE ---