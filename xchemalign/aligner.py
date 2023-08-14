# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import json
import os
from pathlib import Path

import yaml
import gemmi

# Local alignment imports
from ligand_neighbourhood_alignment import constants as lna_constants
from ligand_neighbourhood_alignment.align_xmaps import _align_xmaps
from ligand_neighbourhood_alignment.data import (
    CanonicalSites,
    ChainOutput,
    ConformerSites,
    Dataset,
    DatasetID,
    DatasetOutput,
    LigandBindingEvent,
    LigandBindingEvents,
    LigandID,
    LigandNeighbourhoods,
    LigandOutput,
    Output,
    SiteTransforms,
    SystemData,
    XtalForms,
)

# from ligand neighbourhood_alignment
from ligand_neighbourhood_alignment import dt

# from ligand_neighbourhood_alignment.generate_aligned_structures import _align_structures_from_sites
# from ligand_neighbourhood_alignment.generate_sites_from_components import (
#     get_components,
#     get_conformer_sites_from_components,
#     get_site_transforms,
#     get_sites_from_conformer_sites,
#     get_structures,
#     get_subsite_transforms,
#     get_xtalform_sites_from_canonical_sites,
# )
# from ligand_neighbourhood_alignment.get_alignability import get_alignability
# from ligand_neighbourhood_alignment.get_graph import get_graph
# from ligand_neighbourhood_alignment.get_ligand_neighbourhoods import get_ligand_neighbourhoods
# from ligand_neighbourhood_alignment.cli import _get_assigned_xtalforms

from ligand_neighbourhood_alignment.cli import (
    _load_assemblies,
    _load_xtalforms,
    _load_dataset_assignments,
    _load_ligand_neighbourhoods,
    _load_alignability_graph,
    _load_ligand_neighbourhood_transforms,
    _load_conformer_sites,
    _load_conformer_site_transforms,
    _load_canonical_sites,
    _load_canonical_site_transforms,
    _load_xtalform_sites,
    _load_reference_stucture_transforms,
    _update,
)

from . import utils
from .utils import Constants
from .pdb_xtal import PDBXtal


def try_make(path):
    if not Path(path).exists():
        os.mkdir(path)


def read_yaml(path):
    with open(path, 'r') as f:
        dic = yaml.safe_load(f)

    return dic


def get_datasets_from_crystals(crystals, output_path):
    # dataset_ids = [DatasetID(dtag=dtag) for dtag in crystals]
    # paths to files will be defined like this: upload_1/crystallographic_files/8dz1/8dz1.pdb
    # this is relative to the output_path variable that is defined from the metadata_file.yaml\
    datasets = {}
    reference_datasets = {}
    new_datasets = {}
    for dtag, crystal in crystals.items():
        dataset = dt.Dataset(
            dtag=dtag,
            pdb=str(output_path / crystal[Constants.META_XTAL_FILES][Constants.META_XTAL_PDB][Constants.META_FILE]),
            xmap="",
            mtz=str(
                output_path
                / crystal[Constants.META_XTAL_FILES].get(Constants.META_XTAL_MTZ, {}).get(Constants.META_FILE)
            ),
            # ligand_binding_events=LigandBindingEvents(
            #     ligand_ids=[
            #         LigandID(
            #             dtag=dtag,
            #             chain=binding_event.get(Constants.META_PROT_CHAIN),
            #             residue=binding_event.get(Constants.META_PROT_RES),
            #         )
            #         for binding_event in crystal[Constants.META_XTAL_FILES].get(Constants.META_BINDING_EVENT, {})
            #     ],
            #     ligand_binding_events=[
            #         LigandBindingEvent(
            #             id=0,
            #             dtag=dtag,
            #             chain=binding_event.get(Constants.META_PROT_CHAIN),
            #             residue=binding_event.get(Constants.META_PROT_RES),
            #             xmap=str(output_path / binding_event.get(Constants.META_FILE)),
            #         )
            #         for binding_event in crystal[Constants.META_XTAL_FILES].get(Constants.META_BINDING_EVENT, {})
            #     ],
            # ),
            ligand_binding_events={
                (
                    str(dtag),
                    str(binding_event.get(Constants.META_PROT_CHAIN)),
                    str(binding_event.get(Constants.META_PROT_RES)),
                ): dt.LigandBindingEvent(
                    id=0,
                    dtag=str(dtag),
                    chain=str(binding_event.get(Constants.META_PROT_CHAIN)),
                    residue=str(binding_event.get(Constants.META_PROT_RES)),
                    xmap=str(output_path / binding_event.get(Constants.META_FILE)),
                )
                for binding_event in crystal[Constants.META_XTAL_FILES].get(Constants.META_BINDING_EVENT, {})
            },
        )
        datasets[dtag] = dataset
        if crystal[Constants.META_STATUS] == Constants.META_STATUS_NEW:
            new_datasets[dtag] = dataset
        if crystal.get(Constants.CRYSTAL_REFERENCE):
            reference_datasets[dtag] = dataset

    if (len(datasets) == 0) or (len(datasets) == 0):
        # self.logger.error(f"Did not find any datasets in metadata_file. Exiting.")
        raise Exception

    # self.logger.info(f"Ligand binding events in datasets:")
    for _dataset_id, _dataset in datasets.items():
        _num_binding_events = len(_dataset.ligand_binding_events)
        # self.logger.info(f"\t{_dataset_id.dtag} : Num Ligand binding events: {_num_binding_events}")

    return datasets, reference_datasets, new_datasets


class Aligner:
    def __init__(self, version_dir, metadata, xtalforms, assemblies, logger=None):
        self.version_dir = Path(version_dir)  # e.g. path/to/upload_1
        self.base_dir = self.version_dir.parent  # e.g. path/to
        self.aligned_dir = self.version_dir / Constants.META_ALIGNED_FILES  # e.g. path/to/upload_1/aligned_files
        self.xtal_dir = self.version_dir / Constants.META_XTAL_FILES  # e.g. path/to/upload_1/crystallographic_files
        self.metadata_file = self.version_dir / metadata  # e.g. path/to/upload_1/metadata.yaml
        if xtalforms:
            self.xtalforms_file = xtalforms
        else:
            self.xtalforms_file = self.base_dir / Constants.XTALFORMS_FILENAME  # e.g. path/to/xtalforms.yaml
        if assemblies:
            self.assemblies_file = assemblies
        else:
            self.assemblies_file = self.base_dir / Constants.ASSEMBLIES_FILENAME
        if logger:
            self.logger = logger
        else:
            self.logger = utils.Logger()
        self.errors = []
        self.warnings = []

    def _log_error(self, msg):
        self.logger.error(msg)
        self.errors.append(msg)

    def validate(self):
        if not self.version_dir.exists():
            self._log_error("version dir {} does not exist".format(self.version_dir))
        elif not self.version_dir.is_dir():
            self._log_error("version dir {} is not a directory".format(self.version_dir))
        else:
            p = self.metadata_file
            if not p.exists():
                self._log_error("metadata file {} does not exist".format(p))
            if not p.is_file():
                self._log_error("metadata file {} is not a file".format(p))

            p = Path(self.xtalforms_file)
            if not p.exists():
                self._log_error("xtalforms file {} does not exist".format(p))
            elif not p.is_file():
                self._log_error("xtalforms file {} is not a file".format(p))

        return len(self.errors), len(self.warnings)

    def run(self):
        self.logger.info("Running aligner...")

        input_meta = utils.read_config_file(str(self.metadata_file))

        if not self.aligned_dir.is_dir():
            self.aligned_dir.mkdir()
            self.logger.info("created aligned directory", self.aligned_dir)

        new_meta = self._perform_alignments(input_meta)

        self._write_output(input_meta, new_meta)

    def _write_output(self, collator_dict, aligner_dict):
        # remove this eventually
        with open(self.version_dir / 'aligner_tmp.yaml', "w") as stream:
            yaml.dump(aligner_dict, stream, sort_keys=False, default_flow_style=None)

        collator_dict[Constants.META_XTALFORMS] = aligner_dict[Constants.META_XTALFORMS]
        # collator_dict[Constants.META_ASSEMBLIES] = aligner_dict[Constants.META_ASSEMBLIES]
        collator_dict[Constants.META_CONFORMER_SITES] = aligner_dict[Constants.META_CONFORMER_SITES]
        collator_dict[Constants.META_CANONICAL_SITES] = aligner_dict[Constants.META_CANONICAL_SITES]
        collator_dict[Constants.META_XTALFORM_SITES] = aligner_dict[Constants.META_XTALFORM_SITES]

        xtals = collator_dict[Constants.META_XTALS]
        for k, v in aligner_dict.items():
            if Constants.META_ALIGNED_FILES in v:
                if k in xtals:
                    xtals[k][Constants.META_ASSIGNED_XTALFORM] = v[Constants.META_ASSIGNED_XTALFORM]
                    xtals[k][Constants.META_ALIGNED_FILES] = v[Constants.META_ALIGNED_FILES]
                else:
                    self.logger.warn('crystal {} not found in input. This is very strange.'.format(k))

        with open(self.version_dir / Constants.METADATA_ALIGN_FILENAME, "w") as stream:
            yaml.dump(collator_dict, stream, sort_keys=False, default_flow_style=None)

    def _perform_alignments(self, meta):
        self.logger.info("Performing alignments")

        # Initialize the output directory and create empty
        # jsons in it

        # Add the datasources in the options json and add them to
        # the datasource json
        # visits = meta[lna_constants.META_INPUT]
        crystals = meta[Constants.META_XTALS]
        # Assert that
        if len(crystals) == 0:
            self.logger.error(f"Did not find any crystals in metadata file. Exiting.")
            raise Exception
        previous_output_path = meta.get(Constants.PREVIOUS_OUTPUT_DIR)
        # output_path = Path(meta[Constants.CONFIG_OUTPUT_DIR])
        output_path = self.version_dir

        # Load the previous output dir if there is one
        if previous_output_path:
            source_fs_model = dt.FSModel.from_dir(previous_output_path)
        else:
            source_fs_model = None

        # Load the fs model for the new output dir
        fs_model = dt.FSModel.from_dir(output_path)
        if source_fs_model:
            fs_model.alignments = source_fs_model.alignments
            fs_model.reference_alignments = source_fs_model.reference_alignments

        # symlink old aligned files
        if Path(previous_output_path).resolve() != output_path.resolve():
            fs_model.symlink_old_data()

        # Update the output fs model, creating flat symlinks to old data
        if not output_path.exists():
            os.mkdir(output_path)

        aligned_structure_dir = output_path / lna_constants.ALIGNED_STRUCTURES_DIR
        if not aligned_structure_dir.exists():
            os.mkdir(aligned_structure_dir)

        # Get the datasets
        datasets, reference_datasets, new_datasets = get_datasets_from_crystals(crystals, self.base_dir)

        # Get assemblies
        if source_fs_model:
            assemblies: dict[str, dt.Assembly] = _load_assemblies(
                source_fs_model.assemblies, Path(self.assemblies_file)
            )
        else:
            assemblies = _load_assemblies(fs_model.assemblies, Path(self.assemblies_file))

        # Get xtalforms
        if source_fs_model:
            xtalforms: dict[str, dt.XtalForm] = _load_xtalforms(source_fs_model.xtalforms, Path(self.xtalforms_file))
        else:
            xtalforms = _load_xtalforms(fs_model.xtalforms, Path(self.xtalforms_file))

        # Get the dataset assignments
        if source_fs_model:
            dataset_assignments = _load_dataset_assignments(Path(source_fs_model.dataset_assignments))
        else:
            dataset_assignments = _load_dataset_assignments(Path(fs_model.dataset_assignments))

        # Get Ligand neighbourhoods
        if source_fs_model:
            ligand_neighbourhoods: dict[tuple[str, str, str], dt.Neighbourhood] = _load_ligand_neighbourhoods(
                source_fs_model.ligand_neighbourhoods
            )
        else:
            ligand_neighbourhoods = _load_ligand_neighbourhoods(fs_model.ligand_neighbourhoods)

        # Get alignability graph
        if source_fs_model:
            alignability_graph = _load_alignability_graph(source_fs_model.alignability_graph)
        else:
            alignability_graph = _load_alignability_graph(fs_model.alignability_graph)

        #
        if source_fs_model:
            ligand_neighbourhood_transforms: dict[
                tuple[tuple[str, str, str], tuple[str, str, str]], dt.Transform
            ] = _load_ligand_neighbourhood_transforms(source_fs_model.ligand_neighbourhood_transforms)
        else:
            ligand_neighbourhood_transforms = _load_ligand_neighbourhood_transforms(
                fs_model.ligand_neighbourhood_transforms
            )

        # Get conformer sites
        if source_fs_model:
            conformer_sites: dict[str, dt.ConformerSite] = _load_conformer_sites(source_fs_model.conformer_sites)
        else:
            conformer_sites = _load_conformer_sites(fs_model.conformer_sites)

        #
        if source_fs_model:
            conformer_site_transforms: dict[tuple[str, str], dt.Transform] = _load_conformer_site_transforms(
                source_fs_model.conformer_site_transforms
            )
        else:
            conformer_site_transforms = _load_conformer_site_transforms(fs_model.conformer_site_transforms)

        # Get canonical sites
        if source_fs_model:
            canonical_sites: dict[str, dt.CanonicalSite] = _load_canonical_sites(source_fs_model.canonical_sites)
        else:
            canonical_sites = _load_canonical_sites(fs_model.canonical_sites)

        #
        if source_fs_model:
            canonical_site_transforms: dict[str, dt.Transform] = _load_canonical_site_transforms(
                source_fs_model.conformer_site_transforms
            )
        else:
            canonical_site_transforms = _load_canonical_site_transforms(fs_model.conformer_site_transforms)

        # Get xtalform sites
        if source_fs_model:
            xtalform_sites: dict[str, dt.XtalFormSite] = _load_xtalform_sites(source_fs_model.xtalform_sites)
        else:
            xtalform_sites = _load_xtalform_sites(fs_model.xtalform_sites)

        # Get reference structure transforms
        if source_fs_model:
            reference_structure_transforms: dict[tuple[str, str], dt.Transform] = _load_reference_stucture_transforms(
                source_fs_model.reference_structure_transforms
            )
        else:
            reference_structure_transforms = _load_reference_stucture_transforms(
                fs_model.reference_structure_transforms
            )

        # Run the update
        updated_fs_model = _update(
            fs_model,
            datasets,
            reference_datasets,
            new_datasets,
            assemblies,
            xtalforms,
            dataset_assignments,
            ligand_neighbourhoods,
            alignability_graph,
            ligand_neighbourhood_transforms,
            conformer_sites,
            conformer_site_transforms,
            canonical_sites,
            xtalform_sites,
            reference_structure_transforms,
        )

        # Update the metadata_file with aligned file locations and site information
        new_meta = {}

        # Add the xtalform information
        meta_xtalforms = {}
        xtalforms = read_yaml(updated_fs_model.xtalforms)
        for xtalform_id, xtalform in xtalforms.items():
            xtalform_reference = xtalform["reference"]
            reference_structure = gemmi.read_structure(datasets[xtalform_reference].pdb)  # (xtalform_reference).pdb)
            reference_spacegroup = reference_structure.spacegroup_hm
            reference_unit_cell = reference_structure.cell

            meta_xtalforms[xtalform_id] = {
                Constants.META_XTALFORM_REFERENCE: xtalform_reference,
                Constants.META_XTALFORM_SPACEGROUP: reference_spacegroup,
                Constants.META_XTALFORM_CELL: {
                    "a": reference_unit_cell.a,
                    "b": reference_unit_cell.b,
                    "c": reference_unit_cell.c,
                    "alpha": reference_unit_cell.alpha,
                    "beta": reference_unit_cell.beta,
                    "gamma": reference_unit_cell.gamma,
                },
            }

        new_meta[Constants.META_XTALFORMS] = meta_xtalforms

        # Add the conformer sites
        conformer_sites = read_yaml(updated_fs_model.conformer_sites)
        new_meta[Constants.META_CONFORMER_SITES] = conformer_sites
        # conformer_sites_meta = new_meta[Constants.META_CONFORMER_SITES] = {}
        # for conformer_site_id, conformer_site in conformer_sites.conformer_sites.items():
        #     conformer_sites_meta[conformer_site_id] = {
        #         Constants.META_CONFORMER_SITE_NAME: None,
        #         Constants.META_CONFORMER_SITE_REFERENCE_LIG: {
        #             Constants.META_DTAG: conformer_site.reference_ligand_id.dtag,
        #             Constants.META_CHAIN: conformer_site.reference_ligand_id.chain,
        #             Constants.META_RESIDUE: conformer_site.reference_ligand_id.residue,
        #         },
        #         Constants.META_CONFORMER_SITE_RESIDUES: {
        #             Constants.META_CHAIN: [res.chain for res in conformer_site.residues],
        #             Constants.META_RESIDUE: [res.residue for res in conformer_site.residues],
        #         },
        #         Constants.META_CONFORMER_SITE_MEMBERS: {
        #             Constants.META_DTAG: [lid.dtag for lid in conformer_site.members],
        #             Constants.META_CHAIN: [lid.chain for lid in conformer_site.members],
        #             Constants.META_RESIDUE: [lid.residue for lid in conformer_site.members],
        #         },
        #     }

        # Add the canonical sites
        canonical_sites = read_yaml(fs_model.canonical_sites)
        new_meta[Constants.META_CANONICAL_SITES] = canonical_sites
        # canonical_sites_meta = new_meta[Constants.META_CANONICAL_SITES] = {}
        # for canonical_site_id, canonical_site in zip(canonical_sites.site_ids, canonical_sites.sites):
        #     canonical_sites_meta[canonical_site_id] = {
        #         Constants.META_CANONICAL_SITE_REF_SUBSITE: canonical_site.reference_subsite_id,
        #         Constants.META_CANONICAL_SITE_CONFORMER_SITES: canonical_site.subsite_ids,
        #         Constants.META_CANONICAL_SITE_RESIDUES: {
        #             Constants.META_CHAIN: [res.chain for res in canonical_site.residues],
        #             Constants.META_RESIDUE: [res.residue for res in canonical_site.residues],
        #         },
        #         Constants.META_CANONICAL_SITE_MEMBERS: {
        #             Constants.META_DTAG: [lid.dtag for lid in canonical_site.members],
        #             Constants.META_CHAIN: [lid.chain for lid in canonical_site.members],
        #             Constants.META_RESIDUE: [lid.residue for lid in canonical_site.members],
        #         },
        #     }

        # Add the xtalform sites - note the chain is that of the original crystal structure, NOT the assembly
        xtalform_sites = read_yaml(fs_model.xtalform_sites)
        new_meta[Constants.META_XTALFORM_SITES] = xtalform_sites
        # xtalform_sites_meta = new_meta[Constants.META_XTALFORM_SITES] = {}
        # for xtalform_site_id, xtalform_site in xtalform_sites.xtalform_sites.items():
        #     xtalform_sites_meta[xtalform_site_id] = {
        #         Constants.META_XTALFORM_SITE_XTALFORM_ID: xtalform_site.xtalform_id,
        #         Constants.META_XTALFORM_SITE_CANONICAL_SITE_ID: xtalform_site.site_id,
        #         Constants.META_XTALFORM_SITE_LIGAND_CHAIN: xtalform_site.crystallographic_chain,
        #         Constants.META_XTALFORM_SITE_MEMBERS: {
        #             Constants.META_DTAG: [lid.dtag for lid in xtalform_site.members],
        #             Constants.META_CHAIN: [lid.chain for lid in xtalform_site.members],
        #             Constants.META_RESIDUE: [lid.residue for lid in xtalform_site.members],
        #         },
        #     }

        # Add the output aligned files
        assigned_xtalforms = read_yaml(fs_model.dataset_assignments)

        # for dtag, crystal in crystals.items():
        #     # Skip if no output for this dataset
        #     if dtag not in dataset_output_dict:
        #         continue
        #
        #     crystal_output = new_meta[dtag] = {}
        #
        #     # Otherwise iterate the output data structure, adding the aligned structure,
        #     # artefacts, xmaps and event maps to the metadata_file
        #     assigned_xtalform = assigned_xtalforms.get_xtalform_id(dtag)
        #     crystal_output[Constants.META_ASSIGNED_XTALFORM] = assigned_xtalform
        #
        #     aligned_output = crystal_output[Constants.META_ALIGNED_FILES] = {}
        #     dataset_output = dataset_output_dict[dtag]
        #     for chain_name, chain_output in dataset_output.aligned_chain_output.items():
        #         aligned_chain_output = aligned_output[chain_name] = {}
        #         for ligand_residue, ligand_output in chain_output.aligned_ligands.items():
        #             aligned_ligand_output = aligned_chain_output[ligand_residue] = {}
        #             for site_id, aligned_structure_path in ligand_output.aligned_structures.items():
        #                 aligned_artefacts_path = ligand_output.aligned_artefacts[site_id]
        #                 aligned_event_map_path = ligand_output.aligned_event_maps[site_id]
        #                 aligned_xmap_path = ligand_output.aligned_xmaps[site_id]
        #                 aligned_ligand_output[site_id] = {
        #                     Constants.META_AIGNED_STRUCTURE: aligned_structure_path,
        #                     Constants.META_AIGNED_ARTEFACTS: aligned_artefacts_path,
        #                     Constants.META_AIGNED_EVENT_MAP: aligned_event_map_path,
        #                     Constants.META_AIGNED_X_MAP: aligned_xmap_path,
        #                 }

        for dtag, crystal in crystals.items():
            # Skip if no output for this dataset
            if dtag not in fs_model.alignments:
                continue

            crystal_output = new_meta[dtag] = {}

            # Otherwise iterate the output data structure, adding the aligned structure,
            # artefacts, xmaps and event maps to the metadata_file
            assigned_xtalform = assigned_xtalforms[dtag]
            crystal_output[Constants.META_ASSIGNED_XTALFORM] = assigned_xtalform

            aligned_output = crystal_output[Constants.META_ALIGNED_FILES] = {}
            dataset_output = fs_model.alignments[dtag]
            for chain_name, chain_output in dataset_output.items():
                aligned_chain_output = aligned_output[chain_name] = {}
                for ligand_residue, ligand_output in chain_output.items():
                    aligned_ligand_output = aligned_chain_output[ligand_residue] = {}
                    for site_id, aligned_structure_path in ligand_output.aligned_structures.items():
                        aligned_artefacts_path = ligand_output.aligned_artefacts[site_id]
                        aligned_event_map_path = ligand_output.aligned_event_maps[site_id]
                        aligned_xmap_path = ligand_output.aligned_xmaps[site_id]
                        aligned_ligand_output[site_id] = {
                            Constants.META_AIGNED_STRUCTURE: aligned_structure_path,
                            Constants.META_AIGNED_ARTEFACTS: aligned_artefacts_path,
                            Constants.META_AIGNED_EVENT_MAP: aligned_event_map_path,
                            Constants.META_AIGNED_X_MAP: aligned_xmap_path,
                        }

        new_meta[Constants.META_TRANSFORMS] = {}

        ## Get the observation to conformer site transforms
        ligand_neighbourhood_transforms = read_yaml(fs_model.ligand_neighbourhood_transforms)
        new_meta[Constants.META_TRANSFORMS][
            Constants.META_TRANSFORMS_OBSERVATION_TO_CONFORMER_SITES
        ] = ligand_neighbourhood_transforms
        # new_meta[Constants.META_TRANSFORMS][Constants.META_TRANSFORMS_OBSERVATION_TO_CONFORMER_SITES] = []
        # for ligand_ids, transform in zip(transforms.ligand_ids, transforms.transforms):
        #     transform_record = {
        #         "from": {
        #             Constants.META_DTAG: ligand_ids[1].dtag,
        #             Constants.META_CHAIN: ligand_ids[1].chain,
        #             Constants.META_RESIDUE: ligand_ids[1].residue,
        #     },
        #         "to": {
        #             Constants.META_DTAG: ligand_ids[0].dtag,
        #             Constants.META_CHAIN: ligand_ids[0].chain,
        #             Constants.META_RESIDUE: ligand_ids[0].residue,
        #     },
        #         "transform": {
        #             "vec": transform.vec,
        #             "mat": transform.mat
        #         }
        #     }
        #     new_meta[Constants.META_TRANSFORMS][Constants.META_TRANSFORMS_OBSERVATION_TO_CONFORMER_SITES].append(transform_record)

        ## Get the conformer site to canonical site transforms
        conformer_site_transforms = read_yaml(fs_model.conformer_site_transforms)
        new_meta[Constants.META_TRANSFORMS][
            Constants.META_TRANSFORMS_CONFORMER_SITES_TO_CANON
        ] = conformer_site_transforms
        # new_meta[Constants.META_TRANSFORMS][Constants.META_TRANSFORMS_CONFORMER_SITES_TO_CANON] = []
        # for ligand_ids, transform in zip(site_transforms.conformer_site_transform_ids, site_transforms.conformer_site_transforms):
        #     transform_record = {
        #         "from_conformer_site": ligand_ids[2],
        #         "to_canon_site": ligand_ids[0],
        #         "transform": {
        #             "vec": transform.vec,
        #             "mat": transform.mat
        #         }
        #     }
        #     new_meta[Constants.META_TRANSFORMS][Constants.META_TRANSFORMS_CONFORMER_SITES_TO_CANON].append(transform_record)

        ## Get the canonical site to global transforms
        # new_meta[Constants.META_TRANSFORMS][Constants.META_TRANSFORMS_CANON_SITES_TO_GLOBAL] = []
        # for canon_site_id, transform in zip(site_transforms.canonical_site_transform_ids, site_transforms.canonical_site_transforms):
        #     transform_record = {
        #         "from_canon_site": canon_site_id[1],
        #         "transform": {
        #             "vec": transform.vec,
        #             "mat": transform.mat
        #         }
        #     }
        #     new_meta[Constants.META_TRANSFORMS][Constants.META_TRANSFORMS_CANON_SITES_TO_GLOBAL].append(transform_record)
        #
        # new_meta[Constants.META_TRANSFORMS][Constants.META_TRANSFORMS_GLOBAL_REFERENCE_CANON_SITE_ID] = canonical_sites.reference_site_id

        num_extract_errors = self._extract_components(crystals, new_meta)
        if num_extract_errors:
            self.logger.error("there were problems extracting components. See above for details")

        return new_meta

    def _extract_components(self, crystals, aligner_meta):
        """
        Extract out the required forms of the molecules.
        1. *_apo.pdb - the aligned structure without the ligand
        2. *_apo_solv.pdb - the aligned solvent molecules only
        3. *_apo_desolv.pdb - the aligned structure protein chain only
        4. *_ligand.mol - molfile of the ligand
        5. *_ligand.pdb - PDB of the ligand

        :param meta:
        :return:
        """

        EMPTY_DICT = {}

        num_errors = 0
        ignore_keys = [Constants.META_CONFORMER_SITES, Constants.META_CANONICAL_SITES, Constants.META_XTALFORM_SITES]
        for k1, v1 in aligner_meta.items():  # k = xtal
            if k1 not in ignore_keys and Constants.META_ALIGNED_FILES in v1:
                self.logger.info('handling', k1)
                cif_file = (
                    crystals.get(k1)
                    .get(Constants.META_XTAL_FILES, EMPTY_DICT)
                    .get(Constants.META_XTAL_CIF, EMPTY_DICT)
                    .get(Constants.META_FILE)
                )

                for k2, v2 in v1[Constants.META_ALIGNED_FILES].items():  # chain
                    for k3, v3 in v2.items():  # ligand
                        for k4, v4 in v3.items():  # occurance?
                            pdb = v4[Constants.META_AIGNED_STRUCTURE]
                            self.logger.info("extracting components", k1, k2, k3, k4, pdb)
                            pth = self.version_dir / pdb
                            if not pth.is_file():
                                self.logger.error("can't find file", pth)
                                num_errors += 1
                            else:
                                pdbxtal = PDBXtal(pth, pth.parent)
                                errs = pdbxtal.validate()
                                if errs:
                                    self.logger.error("validation errors - can't extract components")
                                    num_errors += 1
                                else:
                                    pdbxtal.create_apo_file()
                                    pdbxtal.create_apo_solv_desolv()

                                    v4[Constants.META_PDB_APO] = str(pdbxtal.apo_file.relative_to(self.version_dir))
                                    v4[Constants.META_PDB_APO_SOLV] = str(
                                        pdbxtal.apo_solv_file.relative_to(self.version_dir)
                                    )
                                    v4[Constants.META_PDB_APO_DESOLV] = str(
                                        pdbxtal.apo_desolv_file.relative_to(self.version_dir)
                                    )
                                    if cif_file:
                                        pdbxtal.create_ligands(k2, k3, str(self.base_dir / cif_file))
                                        v4[Constants.META_LIGAND_MOL] = (
                                            str(pdbxtal.ligand_base_file.relative_to(self.version_dir)) + '.mol'
                                        )
                                        v4[Constants.META_LIGAND_PDB] = (
                                            str(pdbxtal.ligand_base_file.relative_to(self.version_dir)) + '.pdb'
                                        )
                                        v4[Constants.META_LIGAND_SMILES] = pdbxtal.smiles

        return num_errors


def main():
    parser = argparse.ArgumentParser(description="aligner")

    parser.add_argument("-d", "--version-dir", required=True, help="Path to version dir")
    parser.add_argument("-m", "--metadata_file", default=Constants.METADATA_XTAL_FILENAME, help="Metadata YAML file")
    parser.add_argument("-x", "--xtalforms", help="Crystal forms YAML file")
    parser.add_argument("-a", "--assemblies", help="Assemblies YAML file")
    parser.add_argument("-l", "--log-file", help="File to write logs to")
    parser.add_argument("--log-level", type=int, default=0, help="Logging level")
    parser.add_argument("--validate", action="store_true", help="Only perform validation")

    args = parser.parse_args()
    print("aligner: ", args)

    logger = utils.Logger(logfile=args.log_file, level=args.log_level)

    a = Aligner(args.version_dir, args.metadata_file, args.xtalforms, args.assemblies, logger=logger)
    num_errors, num_warnings = a.validate()

    if not args.validate:
        if num_errors:
            print("There are errors, cannot continue")
            exit(1)
        else:
            a.run()


if __name__ == "__main__":
    main()
