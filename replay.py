import os
import ast
import argparse
import pandas as pd
import xml.etree.ElementTree as ET

def clean_edges_str(raw_edges: str) -> str:
    """Cleans the edge list to match the SUMO XML format."""
    if not isinstance(raw_edges, str):
        return str(raw_edges)
    
    raw_edges = raw_edges.strip()
    if raw_edges.startswith("[") and raw_edges.endswith("]"):
        try:
            edge_list = ast.literal_eval(raw_edges)
            return " ".join(str(e) for e in edge_list)
        except Exception:
            pass

    for char in ["[", "]", "'", '"', ","]:
        raw_edges = raw_edges.replace(char, " ")
    return " ".join(raw_edges.split())

def generate_replay_config(episode_num, records_folder, network_folder, network_name, out_dir, seed=42):
    os.makedirs(out_dir, exist_ok=True)
    
    ep_csv = os.path.join(records_folder, "episodes", f"ep{episode_num}.csv")
    paths_csv = os.path.join(records_folder, "paths.csv")
    
    if not os.path.exists(ep_csv) or not os.path.exists(paths_csv):
        raise FileNotFoundError(f"Files not found for episode {episode_num}.")

    # 1. Load routes from paths.csv
    df_paths = pd.read_csv(paths_csv)
    col_orig = next((c for c in ["origins", "origin", "O", "start_node"] if c in df_paths.columns), None)
    col_dest = next((c for c in ["destinations", "destination", "D", "end_node"] if c in df_paths.columns), None)
    col_edges = next((c for c in ["path", "edges", "route", "edge_list"] if c in df_paths.columns), None)
    col_idx = next((c for c in ["cluster", "action", "path_id", "idx", "path_index"] if c in df_paths.columns), None)

    if not all([col_orig, col_dest, col_edges]):
        raise ValueError("Required columns not found in paths.csv.")
    if col_idx is None:
        df_paths["_action_idx"] = df_paths.groupby([col_orig, col_dest]).cumcount()
        col_idx = "_action_idx"

    routes = []
    route_ids = set()
    for _, row in df_paths.iterrows():
        o, d, idx = int(row[col_orig]), int(row[col_dest]), int(row[col_idx])
        route_id = f"{o}_{d}_{idx}"
        edges_str = clean_edges_str(str(row[col_edges]))
        routes.append(ET.Element("route", {"id": route_id, "edges": edges_str}))
        route_ids.add(route_id)

    # 2. Load vehicles from ep{num}.csv and sort chronologically
    df_ep = pd.read_csv(ep_csv)
    df_ep = df_ep.sort_values("start_time").reset_index(drop=True)
    
    kinds = set(df_ep["kind"].unique()) if "kind" in df_ep.columns else {"human", "machine"}
    
    root = ET.Element("routes")
    for k in kinds:
        ET.SubElement(root, "vType", {"id": str(k)})
    for r in routes:
        root.append(r)

    written = 0
    for _, row in df_ep.iterrows():
        action = row["action"]
        if pd.isna(action) or int(action) < 0:
            continue
            
        route_id = f"{int(row['origin'])}_{int(row['destination'])}_{int(action)}"
        if route_id not in route_ids:
            continue

        ET.SubElement(root, "vehicle", {
            "id": str(row["id"]),
            "type": str(row["kind"]) if "kind" in row else "human",
            "depart": f"{float(row['start_time']):.2f}",
            "route": route_id,
        })
        written += 1

    # 3. Save the .rou.xml file
    rou_file = os.path.join(out_dir, f"replay_ep{episode_num}.rou.xml")
    tree = ET.ElementTree(root)
    if hasattr(ET, "indent"): 
        ET.indent(tree, space="  ")
    tree.write(rou_file, encoding="utf-8", xml_declaration=True)

    # 4. Create the .sumocfg file with RELATIVE paths for Git compatibility
    cfg_file = os.path.join(out_dir, f"replay_ep{episode_num}.sumocfg")
    max_start = float(df_ep["start_time"].max())
    
    # Calculate relative paths from the .sumocfg location (out_dir)
    # This makes the config portable (e.g., ../networks/...)
    out_dir_abs = os.path.abspath(out_dir)
    net_file_abs = os.path.abspath(os.path.join(network_folder, f"{network_name}.net.xml"))
    
    net_file_rel = os.path.relpath(net_file_abs, start=out_dir_abs).replace("\\", "/")
    rou_file_rel = os.path.basename(rou_file) # Just the filename since it's in the same folder as cfg
    
    cfg_root = ET.Element("configuration")
    input_el = ET.SubElement(cfg_root, "input")
    ET.SubElement(input_el, "net-file", {"value": net_file_rel})
    ET.SubElement(input_el, "route-files", {"value": rou_file_rel})
    
    time_el = ET.SubElement(cfg_root, "time")
    ET.SubElement(time_el, "begin", {"value": "0"})
    ET.SubElement(time_el, "end", {"value": str(int(max_start + 3600))}) # 1-hour buffer at the end
    
    processing_el = ET.SubElement(cfg_root, "processing")
    ET.SubElement(processing_el, "time-to-teleport", {"value": "-1"}) 
    
    cfg_tree = ET.ElementTree(cfg_root)
    if hasattr(ET, "indent"): 
        ET.indent(cfg_tree, space="  ")
    cfg_tree.write(cfg_file, encoding="utf-8", xml_declaration=True)

    print(f"Config generated: {cfg_file}")
    print(f"({written} vehicles written, simulation ends at t={int(max_start + 3600)}s)")
    print(f"Note: Paths in .sumocfg are relative. Ensure folder structure is maintained.")
    return cfg_file

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generates a .sumocfg file to replay an episode.")
    parser.add_argument("--episode", type=int, required=True, help="Episode number (e.g., 2400)")
    parser.add_argument("--records", type=str, default="tbrf_ing_self_40avs_44_41", help="Results folder containing episodes/ and paths.csv")
    parser.add_argument("--network-dir", type=str, required=True, help="Network folder (e.g., ../URB/networks/ingolstadt_custom2)")
    parser.add_argument("--network-name", type=str, required=True, help="Network name without .net.xml (e.g., ingolstadt_custom2)")
    parser.add_argument("--out", type=str, default="replays", help="Output directory for the replay files")
    
    args = parser.parse_args()
    generate_replay_config(args.episode, args.records, args.network_dir, args.network_name, args.out)