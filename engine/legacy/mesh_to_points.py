import numpy as np
import trimesh
import sys
import os

def get_meshes_from_file(input_file):
    """
    Loads a file and returns a list of individual meshes.
    Handles both single meshes and Scenes (GLB/GLTF) by extracting parts.
    """
    if not os.path.exists(input_file):
        print(f"Error: File '{input_file}' not found.")
        return []

    print(f"Loading: {input_file}...")
    try:
        loaded = trimesh.load(input_file)
    except Exception as e:
        print(f"Error loading {input_file}: {e}")
        return []

    meshes_to_process = []

    # CASE 1: It's a Scene (e.g., GLB, GLTF, complex OBJ)
    if isinstance(loaded, trimesh.Scene):
        print(f"  -> Detected Scene with {len(loaded.geometry)} geometries.")
        # Iterate through the scene graph to get meshes with their correct world transforms
        for node_name in loaded.graph.nodes_geometry:
            # Get the transform and geometry name for this node
            transform, geometry_name = loaded.graph.get(node_name)
            
            # Get the actual mesh geometry
            mesh = loaded.geometry[geometry_name].copy()
            
            # Apply the transform (move it to its correct place in the car/scene)
            mesh.apply_transform(transform)
            meshes_to_process.append(mesh)
            
    # CASE 2: It's a single Mesh (e.g., STL)
    else:
        print("  -> Detected single Mesh.")
        meshes_to_process.append(loaded)

    return meshes_to_process

def voxelize_and_merge(input_files, output_file, spacing):
    all_points = []
    current_obj_id = 1  # Start Object IDs at 1

    print(f"--- Processing {len(input_files)} file(s) ---")
    print(f"Spacing: {spacing} m")

    for filename in input_files:
        # Get list of meshes from this file
        meshes = get_meshes_from_file(filename)

        for mesh in meshes:
            # 1. Check watertightness (Warn only)
            if not mesh.is_watertight:
                print(f"  [Warning] Object {current_obj_id} is not watertight. Filling might be partial.")

            # 2. Voxelize
            try:
                # Create voxel grid
                voxel_grid = mesh.voxelized(pitch=spacing)
                # Fill inside and get points
                points = voxel_grid.fill().points
            except Exception as e:
                print(f"  [Error] Voxelizing Object {current_obj_id}: {e}")
                continue

            if len(points) == 0:
                print(f"  [Warning] Object {current_obj_id} generated 0 points (maybe too small for spacing?).")
                continue

            # 3. Add Object ID column
            # Create a column filled with the current ID
            id_column = np.full((len(points), 1), current_obj_id)
            
            # Stack [X, Y, Z] + [ID]
            mesh_data = np.hstack((points, id_column))
            
            all_points.append(mesh_data)
            print(f"  -> Object {current_obj_id}: Generated {len(points)} particles.")
            
            # Increment ID for the next part
            current_obj_id += 1

    # SAVE OUTPUT
    if all_points:
        final_data = np.vstack(all_points)
        print(f"--- Saving ---")
        print(f"Total Particles: {len(final_data)}")
        
        # Save X Y Z ID
        np.savetxt(output_file, final_data, fmt='%.6f %.6f %.6f %d')
        print(f"Saved to '{output_file}'")
        print(f"Format: X  Y  Z  Object_ID")
    else:
        print("Error: No particles generated.")

if __name__ == "__main__":
    # --- CONFIGURATION ---
    SPACING = 0.0025          # 2.5mm
    OUTPUT_FILE = "particles.dat"
    # ---------------------

    # Usage: python script.py file1.glb file2.stl ...
    if len(sys.argv) > 1:
        input_files = sys.argv[1:]
    else:
        # Default fallback
        input_files = ["model.glb"]

    voxelize_and_merge(input_files, OUTPUT_FILE, SPACING)