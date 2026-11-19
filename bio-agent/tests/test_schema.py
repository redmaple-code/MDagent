import tests  # Establish the owned src path for unittest discovery.
import copy
import importlib
import unittest

from bioagent.errors import BioError
from tests.support import protein_protocol, water_protocol


class SchemaTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module("bioagent.schema")
        except ModuleNotFoundError:
            self.fail("closed protocol validation is not implemented")

    def test_compute_local_resources_cannot_select_a_host(self):
        self.assertEqual(self.module.validate_protocol(protein_protocol())["domain"], "bio-openmm")
        for key, value in (("location", "remote"), ("platform", "unknown"), ("targetId", "server")):
            protocol = protein_protocol()
            protocol["stages"][0]["resources"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(BioError, "COMPUTE_RESOURCES_REQUIRED"):
                self.module.validate_protocol(protocol)

    def test_cuda_dynamics_keeps_preparation_and_analysis_on_cpu(self):
        protocol = protein_protocol()
        protocol["stages"][2]["resources"].update(platform="CUDA", deviceIndex=0, precision="mixed")
        self.module.validate_protocol(protocol)
        protocol["stages"][0]["resources"].update(platform="CUDA", deviceIndex=0, precision="mixed")
        with self.assertRaisesRegex(BioError, "PREPARATION_ANALYSIS_CPU_REQUIRED"):
            self.module.validate_protocol(protocol)

    def test_unknown_fields_and_nonfinite_values_are_rejected(self):
        for field, value in (("command", "uname"), ("approved", True)):
            protocol = protein_protocol(); protocol[field] = value
            with self.subTest(field=field), self.assertRaises(BioError):
                self.module.validate_protocol(protocol)
        for value in (float("nan"), float("inf"), True):
            protocol = protein_protocol()
            protocol["stages"][0]["resources"]["cpuThreads"] = value
            with self.subTest(value=value), self.assertRaises(BioError):
                self.module.validate_protocol(protocol)
        protocol = protein_protocol(); protocol["stages"][0]["parameters"]["system"]["surprise"] = 1
        with self.assertRaises(BioError): self.module.validate_protocol(protocol)

    def test_cycle_and_duplicate_stage_ids_are_rejected(self):
        protocol = protein_protocol(); protocol["stages"][0]["dependsOn"] = ["analysis"]
        with self.assertRaisesRegex(BioError, "INVALID_DEPENDENCIES"):
            self.module.validate_protocol(protocol)
        protocol = protein_protocol(); protocol["stages"].append(copy.deepcopy(protocol["stages"][0]))
        with self.assertRaisesRegex(BioError, "INVALID_STAGE"):
            self.module.validate_protocol(protocol)

    def test_profile_limits_are_preserved(self):
        self.module.validate_protocol(water_protocol())
        for field, value in (("boxSizeNm", 2), ("forceField", "unknown")):
            protocol = water_protocol(); protocol["stages"][0]["parameters"]["preparation"][field] = value
            with self.subTest(field=field), self.assertRaises(BioError): self.module.validate_protocol(protocol)

    def test_protocol_hash_changes_with_scientific_conditions(self):
        first = protein_protocol(); second = protein_protocol()
        second["stages"][2]["parameters"]["dynamics"]["steps"] = 25
        self.assertNotEqual(self.module.protocol_sha256(first), self.module.protocol_sha256(second))
