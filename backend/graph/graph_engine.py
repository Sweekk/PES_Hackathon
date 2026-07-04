import os
import re
import json
import pandas as pd
import networkx as nx

# -----------------------------------------------------
# AccountParser
# -----------------------------------------------------
class AccountParser:
    def __init__(self):
        pass

    def to_float(self, val):
        if pd.isna(val) or val is None:
            return 0.0
        val_str = str(val).replace(",", "").strip()
        if val_str.lower() in ["", "nan", "none", "null", "-", "cr", "dr"]:
            return 0.0
        try:
            return float(val_str)
        except ValueError:
            return 0.0

    def detect_counterparty(self, desc):
        if not desc:
            return "UNKNOWN_ENTITY"
        desc_str = str(desc).strip()
        
        # Check for IFSC + Acc or Acc pattern
        match = re.search(r"ACC_?\d+|[A-Z]{4}\d+", desc_str, re.IGNORECASE)
        if match:
            return match.group(0).upper().replace(" ", "_")
            
        # Check for UPI VPA
        vpa_match = re.search(r"([a-zA-Z0-9.\-_]+@[a-zA-Z0-9.\-_]+)", desc_str)
        if vpa_match:
            return vpa_match.group(1).upper()
            
        # Fallback to first non-generic word > 3 chars
        words = [w for w in re.split(r"[^a-zA-Z0-9]", desc_str) if len(w) > 3]
        for w in words:
            if w.upper() not in ["UPI", "IMPS", "NEFT", "RTGS", "DEBIT", "CREDIT", "TRANSFER", "TFR", "CASH", "DEP", "WDL", "SELF"]:
                return w.upper()
        return words[0].upper() if words else "UNKNOWN_ENTITY"

    def parse_transactions(self, raw_txs):
        normalized = []
        for tx in raw_txs:
            # Read and sanitize fields
            date = tx.get("Date") or tx.get("date") or "UNKNOWN"
            value_date = tx.get("Value Date") or tx.get("value_date") or date
            desc = tx.get("Narration") or tx.get("description") or "NO_NARRATION"
            
            debit = self.to_float(tx.get("Debit") or tx.get("debit"))
            credit = self.to_float(tx.get("Credit") or tx.get("credit"))
            balance = self.to_float(tx.get("Balance") or tx.get("balance"))
            
            amount = debit if debit > 0 else credit
            
            counterparty = self.detect_counterparty(desc)
            
            if debit > 0:
                from_acc = "MAIN_ACC"
                to_acc = counterparty
            else:
                from_acc = counterparty
                to_acc = "MAIN_ACC"
                
            norm_tx = {
                "date": date,
                "value_date": value_date,
                "description": desc,
                "debit": debit,
                "credit": credit,
                "balance": balance,
                "amount": amount,
                "from_account": from_acc,
                "to_account": to_acc,
                "flagged": tx.get("flagged", False),
                "status": tx.get("status") or "Normal",
                "reference": tx.get("reference") or tx.get("Reference") or ""
            }
            normalized.append(norm_tx)
        return normalized

# -----------------------------------------------------
# GraphBuilder
# -----------------------------------------------------
class GraphBuilder:
    def __init__(self):
        pass

    def build_graph(self, normalized_txs):
        G = nx.DiGraph()
        
        for tx in normalized_txs:
            u = tx["from_account"]
            v = tx["to_account"]
            
            # Add nodes and properties
            if not G.has_node(u):
                G.add_node(u, flagged=False)
            if not G.has_node(v):
                G.add_node(v, flagged=False)
                
            # If the transaction is flagged, propagate flagged status to the node
            if tx.get("flagged"):
                G.nodes[u]["flagged"] = True
                G.nodes[v]["flagged"] = True
                
            # Create/Update directed edge
            if not G.has_edge(u, v):
                G.add_edge(u, v, transactions=[])
                
            edge_data = {
                "amount": tx["amount"],
                "debit": tx["debit"],
                "credit": tx["credit"],
                "balance": tx["balance"],
                "date": tx["date"],
                "value_date": tx["value_date"],
                "description": tx["description"],
                "cheque_no": tx["reference"],
                "consistency_status": tx["status"]
            }
            G[u][v]["transactions"].append(edge_data)
            
            # Keep aggregated fields directly on the edge for easy graph queries
            G[u][v]["amount"] = G[u][v].get("amount", 0.0) + tx["amount"]
            G[u][v]["debit"] = G[u][v].get("debit", 0.0) + tx["debit"]
            G[u][v]["credit"] = G[u][v].get("credit", 0.0) + tx["credit"]
            G[u][v]["balance"] = tx["balance"]
            G[u][v]["date"] = tx["date"]
            G[u][v]["value_date"] = tx["value_date"]
            G[u][v]["description"] = tx["description"]
            G[u][v]["cheque_no"] = tx["reference"]
            G[u][v]["consistency_status"] = tx["status"]
            
        return G

# -----------------------------------------------------
# GraphAnalyzer
# -----------------------------------------------------
class GraphAnalyzer:
    def __init__(self):
        pass

    def graph_summary(self, G):
        num_nodes = G.number_of_nodes()
        num_edges = G.number_of_edges()
        density = nx.density(G) if num_nodes > 1 else 0.0
        
        total_volume = 0.0
        for u, v, data in G.edges(data=True):
            total_volume += data.get("amount", 0.0)
            
        return {
            "num_nodes": num_nodes,
            "num_edges": num_edges,
            "density": density,
            "total_volume": total_volume
        }

    def degree_analysis(self, G):
        in_degrees = dict(G.in_degree())
        out_degrees = dict(G.out_degree())
        degrees = dict(G.degree())
        
        result = {}
        for node in G.nodes():
            result[node] = {
                "in_degree": in_degrees.get(node, 0),
                "out_degree": out_degrees.get(node, 0),
                "total_degree": degrees.get(node, 0)
            }
        return result

    def degree_centrality(self, G):
        if G.number_of_nodes() <= 1:
            return {node: 0.0 for node in G.nodes()}
        return nx.degree_centrality(G)

    def pagerank(self, G):
        if G.number_of_nodes() == 0:
            return {}
        try:
            return nx.pagerank(G, alpha=0.85)
        except Exception:
            return {node: 1.0 / G.number_of_nodes() for node in G.nodes()}

    def betweenness(self, G):
        if G.number_of_nodes() <= 2:
            return {node: 0.0 for node in G.nodes()}
        return nx.betweenness_centrality(G)

# -----------------------------------------------------
# HubDetector
# -----------------------------------------------------
class HubDetector:
    def __init__(self):
        pass

    def detect_hubs(self, G, top_n=5):
        degree_anal = GraphAnalyzer().degree_analysis(G)
        sorted_nodes = sorted(
            degree_anal.items(),
            key=lambda item: item[1]["total_degree"],
            reverse=True
        )
        
        hubs = []
        for node, degs in sorted_nodes[:top_n]:
            hubs.append({
                "account": node,
                "connections": degs["total_degree"],
                "incoming": degs["in_degree"],
                "outgoing": degs["out_degree"]
            })
        return hubs

# -----------------------------------------------------
# NeighborSearch
# -----------------------------------------------------
class NeighborSearch:
    def __init__(self):
        pass

    def get_neighbors(self, G, node):
        if not G.has_node(node):
            return {"incoming": [], "outgoing": []}
            
        return {
            "incoming": list(G.predecessors(node)),
            "outgoing": list(G.successors(node))
        }

# -----------------------------------------------------
# PathFinder
# -----------------------------------------------------
class PathFinder:
    def __init__(self):
        pass

    def find_shortest_path(self, G, source, target):
        if not G.has_node(source) or not G.has_node(target):
            return {
                "status": "error",
                "message": f"One or both nodes ({source}, {target}) do not exist in the graph.",
                "path": []
            }
            
        try:
            path = nx.shortest_path(G, source, target)
            return {
                "status": "success",
                "message": f"Shortest path found from {source} to {target}.",
                "path": path
            }
        except nx.NetworkXNoPath:
            return {
                "status": "no_path",
                "message": f"No path exists between {source} and {target}.",
                "path": []
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Error finding path: {str(e)}",
                "path": []
            }

# -----------------------------------------------------
# ConnectedComponents
# -----------------------------------------------------
class ConnectedComponents:
    def __init__(self):
        pass

    def find_components(self, G):
        undirected_G = G.to_undirected()
        components = list(nx.connected_components(undirected_G))
        
        result = []
        for idx, comp in enumerate(components, start=1):
            result.append({
                "cluster_id": idx,
                "nodes": list(comp),
                "size": len(comp)
            })
        return result

# -----------------------------------------------------
# RoundTripDetector
# -----------------------------------------------------
class RoundTripDetector:
    def __init__(self):
        pass

    def detect_cycles(self, G, max_cycles=50):
        try:
            cycle_gen = nx.simple_cycles(G)
            cycles = []
            for cycle in cycle_gen:
                if len(cycle) > 1:
                    cycles.append(cycle)
                    if len(cycles) >= max_cycles:
                        break
            return cycles
        except Exception as e:
            print(f"Error detecting cycles: {e}")
            return []

# -----------------------------------------------------
# GraphExporter
# -----------------------------------------------------
class GraphExporter:
    def __init__(self):
        pass

    def export_graph(self, G, pagerank_dict=None):
        nodes_list = []
        for node in G.nodes():
            node_data = {
                "id": node,
                "flagged": G.nodes[node].get("flagged", False)
            }
            if pagerank_dict and node in pagerank_dict:
                node_data["pagerank"] = pagerank_dict[node]
            nodes_list.append(node_data)
            
        edges_list = []
        for u, v, data in G.edges(data=True):
            edge_data = {
                "from": u,
                "to": v,
                "amount": data.get("amount", 0.0),
                "debit": data.get("debit", 0.0),
                "credit": data.get("credit", 0.0),
                "balance": data.get("balance", 0.0),
                "date": data.get("date", ""),
                "description": data.get("description", ""),
                "consistency_status": data.get("consistency_status", "Normal")
            }
            edges_list.append(edge_data)
            
        return {
            "nodes": nodes_list,
            "edges": edges_list
        }

    def save_json(self, G, output_path):
        data = self.export_graph(G)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

# -----------------------------------------------------
# GraphEngine
# -----------------------------------------------------
class GraphEngine:
    def __init__(self, raw_transactions):
        # AccountParser
        parser = AccountParser()
        self.normalized_txs = parser.parse_transactions(raw_transactions)
        
        # GraphBuilder
        builder = GraphBuilder()
        self.G = builder.build_graph(self.normalized_txs)
        
        # Instantiate analytical submodules
        self.analyzer = GraphAnalyzer()
        self.hub_detector = HubDetector()
        self.neighbor_search = NeighborSearch()
        self.path_finder = PathFinder()
        self.components_finder = ConnectedComponents()
        self.round_trip_detector = RoundTripDetector()
        self.exporter = GraphExporter()

    def get_summary(self):
        return self.analyzer.graph_summary(self.G)

    def get_degree_analysis(self):
        return self.analyzer.degree_analysis(self.G)

    def get_degree_centrality(self):
        return self.analyzer.degree_centrality(self.G)

    def get_pagerank(self):
        return self.analyzer.pagerank(self.G)

    def get_betweenness(self):
        return self.analyzer.betweenness(self.G)

    def get_components(self):
        return self.components_finder.find_components(self.G)

    def get_hubs(self, top_n=5):
        return self.hub_detector.detect_hubs(self.G, top_n=top_n)

    def get_round_trips(self, max_cycles=50):
        return self.round_trip_detector.detect_cycles(self.G, max_cycles=max_cycles)

    def get_shortest_path(self, source, target):
        return self.path_finder.find_shortest_path(self.G, source, target)

    def get_graph_data(self):
        pr = self.get_pagerank()
        return self.exporter.export_graph(self.G, pagerank_dict=pr)
