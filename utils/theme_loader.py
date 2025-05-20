import os
import importlib.util
import sys
from typing import Dict, Callable

def load_theme_functions(theme_name: str) -> Dict[str, Callable]:
    """Dynamically load all functions from theme's function directory"""
    theme_functions = {}
    functions_dir = os.path.join('themes', theme_name, 'functions')
    
    if not os.path.exists(functions_dir):
        return theme_functions

    for filename in os.listdir(functions_dir):
        if filename.endswith('.py') and not filename.startswith('__'):
            module_name = f"theme_functions_{filename[:-3]}"  # Remove .py extension
            module_path = os.path.join(functions_dir, filename)
            
            try:
                spec = importlib.util.spec_from_file_location(module_name, module_path)
                if spec is not None and spec.loader is not None:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)
                    
                    # Get all callable attributes that don't start with underscore
                    functions = {name: getattr(module, name) 
                               for name in dir(module) 
                               if callable(getattr(module, name)) and not name.startswith('_')}
                    
                    theme_functions.update(functions)
            except Exception as e:
                print(f"Error loading {filename}: {str(e)}")
                
    return theme_functions