import os
import importlib.util
import sys
import shutil
from typing import Dict, Callable, Tuple


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
	
	
	
	
def copy_theme_static_files(theme_name: str, main_static_dir: str = 'static') -> bool:
	theme_static_dir = os.path.join('themes', theme_name, 'static')
	
	if not os.path.exists(theme_static_dir):
		print(f"No static directory found for theme '{theme_name}'")
		return False
	

	if not os.path.exists(main_static_dir):
		return
		#os.makedirs(main_static_dir)
	
	# Create theme-specific folder in main static directory
	theme_target_dir = os.path.join(main_static_dir, theme_name)
	
	try:
		# Remove existing theme static files if they exist
		if os.path.exists(theme_target_dir):
			shutil.rmtree(theme_target_dir)
		
		# Copy theme static files
		shutil.copytree(theme_static_dir, theme_target_dir)
		print(f"Static files for theme '{theme_name}' copied to '{theme_target_dir}'")
		return True
		
	except Exception as e:
		print(f"Error copying static files for theme '{theme_name}': {str(e)}")
		return False

def load_theme(theme_name: str, main_static_dir: str = 'static') -> Tuple[Dict[str, Callable], bool]:
	"""Load both functions and static files for a theme"""
	functions = load_theme_functions(theme_name)
	static_success = copy_theme_static_files(theme_name, main_static_dir)
	
	return functions, static_success
