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

import argparse, os, shutil
import yaml

from . import utils, validator


_METADATA_FILENAME = 'metadata.yaml'
_CONFIG_FILENAME = 'config.yaml'
_VERSION_DIR_PREFIX = 'upload_'


class Collator:

    def __init__(self, config_file, logger=None):
        self.config_file = config_file

        with open(config_file, 'r') as stream:
            config = yaml.safe_load(stream)

        self.base_dir = config['base_dir']
        self.input_dirs = config['input_dirs']
        self.output_dir = config['output_dir']
        self.target_name = config['target_name']
        self.config = config
        self.version_dir = None
        self.meta_history = []
        self.all_xtals = None
        self.new_or_updated_xtals = None
        if logger:
            self.logger = logger
        else:
            self.logger = utils.Logger()

    def validate(self):
        v = validator.Validator(self.base_dir, self.input_dirs, self.output_dir, self.target_name, logger=self.logger)
        meta = v.validate_all()
        infos, warnings, errors = self.logger.get_num_messages()
        return meta, warnings, errors

    def run(self, meta):
        self.logger.log('Running collator...', level=0)
        v_dir = self._read_versions()
        if not v_dir:
            self.logger.log('Error with version dir. Please fix and try again.', level=2)
            return None
        self.logger.log('Using version dir {}'.format(v_dir), level=0)
        self.logger.log('Coping files ...', level=0)
        new_meta = self._copy_files(meta)
        self.logger.log('Munging the history ...', level=0)
        all_xtals, new_xtals = self._munge_history(meta)
        self.logger.log('Writing metadata ...', level=0)
        self._write_metadata(new_meta, all_xtals, new_xtals)
        self.logger.log('Copying config ...', level=0)
        self._copy_config()
        self.logger.log('Run complete', level=0)
        return new_meta

    def _read_versions(self):
        # find out which version dirs exist
        version = 1
        while True:
            v_dir = os.path.join(self.output_dir, _VERSION_DIR_PREFIX + str(version))
            if os.path.isdir(v_dir):
                version += 1
            else:
                break
        if version == 1:
            self.logger.log('No version directory found. Please create one named upload_1', level=2)
            return None

        # the working version dir is one less than the current value
        version -= 1
        self.logger.log('Version is {}'.format(version), level=0)
        v_dir = os.path.join(self.output_dir, _VERSION_DIR_PREFIX + str(version))

        # read the metadata from the earlier versions
        if version > 1:
            for v in range(1, version):
                self.logger.log('Reading metadata for version {}'.format(v), level=0)
                meta_file = os.path.join(self.output_dir, _VERSION_DIR_PREFIX + str(v), _METADATA_FILENAME)
                if os.path.isfile(meta_file):
                    with open(meta_file, 'r') as stream:
                        meta = yaml.safe_load(stream)
                        self.meta_history.append(meta)
                else:
                    self.logger.log('Metadata file {} not found'.format(meta_file), level=2)
                    return None

        self.version_dir = v_dir

        num_old_metas = len(self.meta_history)
        if num_old_metas:
            self.logger.log('Found {} metadata files from previous versions'.format(num_old_metas), level=0)

        return v_dir

    def _copy_files(self, meta):
        cryst_dir = os.path.join(self.version_dir, 'crystallographic')
        self.logger.log('Using cryst_dir of', cryst_dir, level=0)
        if os.path.exists(cryst_dir):
            self.logger.log('removing old cryst_dir', level=0)
            shutil.rmtree(cryst_dir)
        self.logger.log('creating cryst_dir', level=0)
        os.makedirs(cryst_dir)

        for name, data in meta['crystals'].items():
            dir = os.path.join(cryst_dir, name)
            os.makedirs(dir)

            xtal_files = data['crystallographic_files']

            # handle the PDB file
            pdb = xtal_files['xtal_pdb']
            if pdb:
                pdb_input = validator.prepend_base(self.base_dir, pdb)
                pdb_output = os.path.join(dir, name + '.pdb')
                f = shutil.copy2(pdb_input, pdb_output, follow_symlinks=True)
                if not f:
                    self.logger.log('Failed to copy PDB file {} to {}'.format(pdb_input, pdb_output), level=2)
                    return None
                digest = utils.gen_sha256(pdb_output)
                xtal_files['xtal_pdb'] = {'file': pdb_output, 'sha256': digest}
            else:
                self.logger.log('PDB entry missing for {}'.format(name), level=1)

            # handle the MTZ file
            mtz = xtal_files['xtal_mtz']
            if mtz:
                mtz_input = validator.prepend_base(self.base_dir, mtz)
                mtz_output = os.path.join(dir, name + '.mtz')
                f = shutil.copy2(mtz_input, mtz_output, follow_symlinks=True)
                if not f:
                    self.logger.log('Failed to copy MTZ file {} to {}'.format(mtz_input, mtz_output), level=2)
                    return None
                digest = utils.gen_sha256(mtz_output)
                xtal_files['xtal_mtz'] = {'file': mtz_output, 'sha256': digest}
            else:
                self.logger.log('MTZ entry missing for {}'.format(name), level=1)

            # handle the CIF file
            cif = xtal_files['ligand_cif']
            if cif:
                cif_input = validator.prepend_base(self.base_dir, cif)
                cif_output = os.path.join(dir, name + '.cif')
                f = shutil.copy2(cif_input, cif_output, follow_symlinks=True)
                if not f:
                    self.logger.log('Failed to copy CIF file {} to {}'.format(cif_input, cif_output), level=2)
                    return None
                digest = utils.gen_sha256(cif_output)
                xtal_files['ligand_cif'] = {'file': cif_output, 'sha256': digest}

                # # convert ligand PDB to SDF
                # # The ligand CIF file does not seem to be readable using OpenBabel so we resort to using the PDB
                # # that also seems to be generated but is not referenced in the database
                # sdf_file = os.path.join(dir, name + '.sdf')
                # ligand_pdb = cif_input[:-4] + '.pdb'
                # if os.path.isfile(ligand_pdb):
                #     count = obabel_utils.convert_molecules(ligand_pdb, 'pdb', sdf_file, 'sdf')
                #     if count:
                #         digest = utils.gen_sha256(sdf_file)
                #         xtal_files['ligand_sdf'] = {'file': sdf_file, 'sha256': digest}
                #     else:
                #         self.logger.log('Ligand SDF file was not generated', level=1)
                # else:
                #     self.logger.log('Ligand PDB file {} not found'.format(ligand_pdb), level=1)
            else:
                self.logger.log('CIF entry missing for {}'.format(name), level=1)

        return meta

    def _munge_history(self, meta):
        all_xtals = {}
        new_or_updated_xtals = {}

        # handle any user defined deprecations
        if 'overrides' in self.config and 'deprecations' in self.config['overrides']:
            deprecations = self.config['overrides']['deprecations']
        else:
            deprecations = {}
        self.logger.info('{} deprecations were defined'.format(len(deprecations)))

        count = 0
        for metad in self.meta_history:
            count += 1
            self.logger.info('Munging metadata {}'.format(count))
            xtals = metad['crystals']
            total = 0
            for xtal_name, xtal_data in xtals.items():
                total += 1
                all_xtals[xtal_name] = xtal_data
            self.logger.info('Metadata {} has {} items'.format(count, total))

        count += 1
        self.logger.info('Munging current metadata')
        xtals = meta['crystals']
        total = 0
        for xtal_name, xtal_data in xtals.items():
            total += 1
            if xtal_name in all_xtals:
                old_xtal_data = all_xtals[xtal_name]
                old_date = old_xtal_data['last_updated']
                new_date = xtal_data['last_updated']
                if not old_date or not new_date:
                    self.logger.warn('Dates not defined for {}, must assume xtal is updated {} {}'.format(xtal_name, ))
                    xtal_data['status'] = 'supersedes'
                    new_or_updated_xtals[xtal_name] = xtal_data
                elif utils.to_datetime(new_date) > utils.to_datetime(old_date):
                    self.logger.info('Xtal {} is updated'.format(xtal_name))
                    xtal_data['status'] = 'supersedes'
                    new_or_updated_xtals[xtal_name] = xtal_data
                else:
                    # self.logger.info('Xtal {} is unchanged'.format(xtal_name))
                    xtal_data['status'] = 'unchanged'
            else:
                xtal_data['status'] = 'new'
                new_or_updated_xtals[xtal_name] = xtal_data
            all_xtals[xtal_name] = xtal_data

            # look for any deprecations
            if xtal_name in deprecations:
                xtal_data['status'] = 'deprecated'
                xtal_data['reason'] = deprecations[xtal_name]
                self.logger.info('Deprecating xtal {}'.format(xtal_name))

        self.logger.info('Metadata {} has {} items'.format(count, total))
        self.logger.info('Munging resulted in {} total xtals, {} are new or updated'.format(
            len(all_xtals), len(new_or_updated_xtals)))

        self.all_xtals = all_xtals
        self.new_or_updated_xtals = new_or_updated_xtals
        return all_xtals, new_or_updated_xtals

    def _write_metadata(self, meta, all_xtals, new_xtals):
        f = os.path.join(self.version_dir, _METADATA_FILENAME)
        with open(f, 'w') as stream:
            yaml.dump(meta, stream, sort_keys=False)
        f = os.path.join(self.version_dir, 'all_xtals.yaml')
        with open(f, 'w') as stream:
            yaml.dump(all_xtals, stream, sort_keys=False)
            f = os.path.join(self.version_dir, 'new_xtals.yaml')
        with open(f, 'w') as stream:
            yaml.dump(new_xtals, stream, sort_keys=False)

    def _copy_config(self):
        f = shutil.copy2(self.config_file, self.version_dir)
        if not f:
            print('Failed to copy config file to {}'.format(self.version_dir))
            return False
        return True


def main():

    parser = argparse.ArgumentParser(description='processor')

    parser.add_argument('-c', '--config-file', required=True, help="Configuration file")
    parser.add_argument('-l', '--log-file', help="Sqlite DB file")
    parser.add_argument('--log-level', type=int, default=0, help="Logging level")
    parser.add_argument('--validate', action='store_true', help='Only perform validation')

    args = parser.parse_args()
    print("processor: ", args)

    logger = utils.Logger(logfile=args.log_file, level=args.log_level)

    p = Collator(args.config_file, logger=logger)

    meta, warnings, errors = p.validate()

    if not args.validate:
        if errors:
            print('There are errors, cannot continue')
            exit(1)
        else:
            p.run(meta)


if __name__ == "__main__":
    main()
