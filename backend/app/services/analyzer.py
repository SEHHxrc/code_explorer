# -*- coding: utf-8 -*-
import os


def build_file_tree(target_dir: str) -> list:
  """静态分析引擎：递归构建文件树"""

  def _scan(root_path):
    tree = []
    try:
      for entry in os.scandir(root_path):
        if entry.name.startswith("."):
          continue
        node = {
            "name": entry.name,
            "path": os.path.relpath(entry.path, target_dir),
            "is_dir": entry.is_dir(),
        }
        if entry.is_dir():
          node["children"] = _scan(entry.path)
        tree.append(node)
    except Exception as e:
      print(f"Error reading dir {root_path}: {e}")
    return tree

  return _scan(target_dir)