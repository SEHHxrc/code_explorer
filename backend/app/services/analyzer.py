# -*- coding: utf-8 -*-
import os


def build_file_tree_with_symbols(target_dir: str, file_symbols_map: dict) -> list:
  """递归生成文件树，将 symbols 直接注入到对应的文件节点中"""

  def _scan(root_path):
    tree = []
    try:
      for entry in os.scandir(root_path):
        if entry.name.startswith("."):
          continue
        rel_path = os.path.relpath(entry.path, target_dir).replace("\\", "/")
        node = {
            "name": entry.name,
            "path": rel_path,
            "is_dir": entry.is_dir(),
        }
        if entry.is_dir():
          node["children"] = _scan(entry.path)
        else:
            # 如果是文件，直接附带该文件的 symbols 列表！
          node["symbols"] = file_symbols_map.get(rel_path, [])
        tree.append(node)
    except Exception as e:
      print(f"Error reading dir {root_path}: {e}")
    return tree

  return _scan(target_dir)