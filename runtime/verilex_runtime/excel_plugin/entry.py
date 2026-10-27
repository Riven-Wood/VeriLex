"""Standalone source/PyInstaller entry with no application startup imports."""
import sys
import types
from pathlib import Path
root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(root))
package = types.ModuleType('verilex_runtime')
package.__path__ = [str(root / 'verilex_runtime')]
sys.modules['verilex_runtime'] = package
from verilex_runtime.excel_plugin.server import main
if __name__ == '__main__':
    main()
