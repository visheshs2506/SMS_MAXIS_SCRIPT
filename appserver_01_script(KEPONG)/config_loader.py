import yaml
import os

class ConfigLoader:
    def __init__(self, path=None):
        if path is None:
            # Always load config.yaml from the parent directory of this file
            base_dir = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(base_dir, "config.yaml")
        self.path = path
        self.last_modified = 0
        self.config_data = {}
        self.load_config()

    def load_config(self):
        with open(self.path, "r") as file:
            self.config_data = yaml.safe_load(file)
        self.last_modified = os.path.getmtime(self.path)

    def get_config(self):
        current_mod_time = os.path.getmtime(self.path)
        if current_mod_time != self.last_modified:
            self.load_config()
        return self.config_data

config_loader = ConfigLoader()

