import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from core import bsp
from features.dat_editing import compiler_strategy, legacy_ed, terrain_semantics


class CompilerStrategyTests(unittest.TestCase):
    def test_rejects_later_pcworldpacker_v85_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "PCWorldPacker.cpp")
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    "#define CURRENT_DAT_VERSION\t85\n"
                    "// 85 - Added the world offset\n"
                    "void SaveFastLight_LightGrid();\n"
                    "void SaveRenderData();\n"
                    "void SaveBlindObjectData();\n"
                    "int main(){ return m_vWorldOffset.x; }\n"
                )

            candidate = compiler_strategy.evaluate_candidate(
                compiler_strategy.analyze_pcworldpacker_source(path)
            )

        self.assertEqual(candidate.expected_dat_version, 85)
        self.assertEqual(candidate.status, "incompatible")
        self.assertTrue(any("not MM9 v66" in item for item in candidate.blockers))
        self.assertTrue(any("world-offset" in item for item in candidate.evidence))

    def test_builtin_partial_tools_do_not_count_as_full_world_compilers(self):
        report = compiler_strategy.build_compiler_strategy_report(lithtech_root=os.path.join(ROOT, "_missing_lithtech"))
        by_id = {candidate.candidate_id: candidate for candidate in report.candidates}

        self.assertEqual(report.compatible_candidate_ids, ())
        self.assertEqual(report.recommendation, "continue_internal_v66_rebuild_pipeline")
        self.assertEqual(by_id["mm9_editor_minimal_bsp_compiler"].status, "partial")
        self.assertTrue(any(
            "does not compile complete MM9 world DAT files" in item
            for item in by_id["mm9_editor_minimal_bsp_compiler"].blockers
        ))
        self.assertEqual(by_id["legacy_ed_reader"].status, "partial")
        self.assertEqual(by_id["lta_reader"].status, "partial")
        self.assertEqual(by_id["lithtech_pcworldpacker"].status, "missing")
        self.assertEqual(by_id["ltworldconverter"].status, "missing")

    def test_full_v66_candidate_requires_all_derived_systems(self):
        compatible = compiler_strategy.evaluate_candidate(
            compiler_strategy.CompilerCandidate(
                candidate_id="hypothetical_mm9_talon_packer",
                name="Hypothetical MM9 Talon packer",
                source="external",
                input_formats=("ed",),
                output_scope="full_world_dat",
                expected_dat_version=66,
                can_compile_full_world=True,
                rebuilt_systems=compiler_strategy.REQUIRED_FULL_WORLD_SYSTEMS,
            )
        )
        incomplete = compiler_strategy.evaluate_candidate(
            compiler_strategy.CompilerCandidate(
                candidate_id="almost_mm9_talon_packer",
                name="Almost MM9 Talon packer",
                source="external",
                input_formats=("ed",),
                output_scope="full_world_dat",
                expected_dat_version=66,
                can_compile_full_world=True,
                rebuilt_systems=("header_v66", "world_tree", "Terrain*"),
            )
        )

        self.assertEqual(compatible.status, "compatible")
        self.assertEqual(compatible.blockers, ())
        self.assertEqual(incomplete.status, "incompatible")
        self.assertTrue(any("does not rebuild required systems" in item for item in incomplete.blockers))

    def test_formats_strategy_report_for_manifest_or_docs(self):
        report = compiler_strategy.build_compiler_strategy_report(lithtech_root=os.path.join(ROOT, "_missing_lithtech"))

        text = compiler_strategy.format_strategy_report(report)

        self.assertIn("DAT compiler strategy", text)
        self.assertIn("recommendation: continue_internal_v66_rebuild_pipeline", text)
        self.assertIn("mm9_editor_minimal_bsp_compiler", text)
        self.assertIn("required systems:", text)

    def test_mm9_dat_to_jupiter_probe_is_classified_as_diagnostic_not_compiler(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mm9_dat_to_jupiter_probe.cpp")
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    "const UInt32 MM9_DAT_VERSION = 66;\n"
                    "bool convertV66WorldModels();\n"
                    "void generatedRender();\n"
                    "const char* a = \"ObjectDataPos RenderDataPos Jupiter\";\n"
                )

            candidate = compiler_strategy.evaluate_candidate(
                compiler_strategy.analyze_mm9_dat_to_jupiter_probe_source(path)
            )

        self.assertEqual(candidate.status, "partial")
        self.assertTrue(any("diagnostic converter/probe" in item for item in candidate.blockers))
        self.assertTrue(any("reads MM9 DAT version 66" in item for item in candidate.evidence))

    def test_lith21_processor_exe_is_unverified_black_box_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Processor.exe")
            with open(path, "wb") as f:
                f.write(
                    b"Processing %s.ed\x00"
                    b"Invalid ED file version\x00"
                    b"%s.dat\x00"
                    b"DAT file %s is an invalid version\x00"
                    b"PhysicsBSP\x00VisBSP\x00WorldTree nodes\x00"
                    b"Lightmap Grid Size\x00Creating physics BSP\x00"
                    b"Creating visibility BSP\x00Number of objects\x00"
                )

            candidate = compiler_strategy.evaluate_candidate(
                compiler_strategy.analyze_lith21_processor_executable(path)
            )

        self.assertEqual(candidate.status, "unverified")
        self.assertEqual(candidate.expected_dat_version, 66)
        self.assertTrue(candidate.can_compile_full_world)
        self.assertEqual(candidate.rebuilt_systems, compiler_strategy.REQUIRED_FULL_WORLD_SYSTEMS)
        self.assertTrue(any("ED world input" in item for item in candidate.evidence))
        self.assertTrue(any("golden harness" in item for item in candidate.blockers))

    def test_ltworldconverter_shogo_branch_is_classified_as_dat_to_ed_research_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as f:
                f.write(
                    "LithTech's DAT to LTA / ED world format converter\n"
                    "Version 56 is supported (Blood 2 and Shogo)\n"
                )
            with open(os.path.join(tmp, "LTWorldConv.lpr"), "w", encoding="utf-8") as f:
                f.write(
                    "Input DAT world file. Version 56 is supported. "
                    "g_bConvertToED := HasOption('e', ''); "
                    "Convert into ED format instead of LTA. "
                    "TEDWorldExporter.Create(WorldReader, slClassesWithBrushes); "
                    "EDExporter.ExportFile(strFilename + '.ed'); "
                    "LTAExporter.ExportText(strFilename + '.lta'); "
                    "CLASSES_WITH_BRUSHES_FILENAME = 'classes_with_brushes.txt';\n"
                )
            with open(os.path.join(tmp, "ltworldreader.pas"), "w", encoding="utf-8") as f:
                f.write("WorldHeader.dwObjectDataPos; WorldHeader.dwRenderDataPos; ReadWorldTree; ReadObjects; ReadRenderData;\n")
            with open(os.path.join(tmp, "ltaworldexporter.pas"), "w", encoding="utf-8") as f:
                f.write("RemoveWorldModel(BSP_VIS); RemoveWorldModel(BSP_PHYSICS); BuildPolyBrushObject; BuildSimpleBrushObject;\n")
            with open(os.path.join(tmp, "edworldexporter.pas"), "w", encoding="utf-8") as f:
                f.write(
                    "ED_VERSION = 1247; "
                    "procedure TEDWorldExporter.WriteHeader; "
                    "procedure TEDWorldExporter.WriteBrushNode; "
                    "m_pExportStream.WriteDWord(ED_VERSION);\n"
                )
            with open(os.path.join(tmp, "classes_with_brushes.txt"), "w", encoding="utf-8") as f:
                f.write("WorldModel\n")

            candidate = compiler_strategy.evaluate_candidate(
                compiler_strategy.analyze_ltworldconverter_source(tmp)
            )

        self.assertEqual(candidate.candidate_id, "ltworldconverter")
        self.assertEqual(candidate.status, "incompatible")
        self.assertEqual(candidate.expected_dat_version, 56)
        self.assertEqual(candidate.output_scope, "dat_to_ed_lta_research_converter")
        self.assertTrue(any("DAT version 56" in item for item in candidate.evidence))
        self.assertTrue(any("binary ED exporter writes ED version 1247" in item for item in candidate.evidence))
        self.assertTrue(any("input DAT reader targets DAT version 56" in item for item in candidate.blockers))
        self.assertTrue(any("ED_VERSION=1247" in item for item in candidate.blockers))

    def test_ltworldconverter_ed_writer_gap_report_captures_v1249_port_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            lt_root = os.path.join(tmp, "LTWorldConverter")
            ed_root = os.path.join(tmp, "EDUnpacker")
            os.makedirs(lt_root)
            os.makedirs(ed_root)
            with open(os.path.join(lt_root, "README.md"), "w", encoding="utf-8") as f:
                f.write("Version 56 is supported (Blood 2 and Shogo)\n")
            with open(os.path.join(lt_root, "ltworldtypes.pas"), "w", encoding="utf-8") as f:
                f.write(
                    "PT_STRING = 0; PT_VECTOR = 1; PT_COLOR = 2; PT_REAL = 3; "
                    "PT_FLAGS = 4; PT_BOOL = 5; PT_LONGINT = 6; PT_ROTATION = 7;\n"
                )
            with open(os.path.join(lt_root, "ltworlddata.pas"), "w", encoding="utf-8") as f:
                f.write(
                    "m_fUV1: LTVector; m_fUV2: LTVector; m_fUV3: LTVector;\n"
                    "property UVData1: LTVector read m_vUV1 write m_vUV1;\n"
                    "property UVData2: LTVector read m_vUV2 write m_vUV2;\n"
                    "property UVData3: LTVector read m_vUV3 write m_vUV3;\n"
                )
            with open(os.path.join(lt_root, "edworldexporter.pas"), "w", encoding="utf-8") as f:
                f.write(
                    "const ED_VERSION = 1247;\n"
                    "BRUSH_PROP_NAME_STR = 'Name'; BRUSH_PROP_POS_STR = 'Pos';\n"
                    "BRUSH_PROP_ROTATION_STR = 'Rotation'; BRUSH_PROP_SOLID_STR = 'Solid';\n"
                    "BRUSH_PROP_NONEXISTANT_STR = 'Nonexistant'; BRUSH_PROP_INVISIBLE_STR = 'Invisible';\n"
                    "BRUSH_PROP_TRANSLUCENT_STR = 'Translucent'; BRUSH_PROP_SKYPORTAL_STR = 'SkyPortal';\n"
                    "BRUSH_PROP_FULLYBRIGHT_STR = 'FullyBright'; BRUSH_PROP_FLATSHADE_STR = 'FlatShade';\n"
                    "BRUSH_PROP_GOURAUDSHADE_STR = 'GouraudShade'; BRUSH_PROP_LIGHTMAP_STR = 'LightMap';\n"
                    "BRUSH_PROP_SUBDIVIDE_STR = 'Subdivide'; BRUSH_PROP_HULLMAKER_STR = 'HullMaker';\n"
                    "BRUSH_PROP_ALWAYSLIGHTMAP_STR = 'AlwaysLightMap'; BRUSH_PROP_DIRECTIONALLIGHT_STR = 'DirectionalLight';\n"
                    "BRUSH_PROP_PORTAL_STR = 'Portal'; BRUSH_PROP_NOSNAP_STR = 'NoSnap';\n"
                    "BRUSH_PROP_SKYPAN_STR = 'SkyPan'; BRUSH_PROP_DETAILLEVEL_STR = 'DetailLevel';\n"
                    "BRUSH_PROP_EFFECT_STR = 'Effect'; BRUSH_PROP_EFFECTPARAM_STR = 'EffectParam';\n"
                    "BRUSH_PROP_FRICTIONCOEFFICIENT_STR = 'FrictionCoefficient';\n"
                    "procedure WriteHeader; begin m_pExportStream.WriteDWord(ED_VERSION); end;\n"
                    "procedure WriteSimpleBrush; var aFloat5: array[0..4] of LTFloat; begin\n"
                    "m_pExportStream.WriteBuffer(aFloat5, SizeOf(LTFloat) * 5);\n"
                    "m_pExportStream.WriteDWord(0); str := pSurface.m_szTextureName;\n"
                    "m_pExportStream.WriteDWord(0); m_pExportStream.WriteBuffer(g_anShade, 3); end;\n"
                    "procedure WriteRootNode; begin end; procedure WriteBrushGroupNode; begin end;\n"
                    "procedure WriteBrushNode; begin end; procedure WriteObjectNode; begin end;\n"
                    "procedure WriteProperty; begin end;\n"
                )
            with open(os.path.join(ed_root, "ed.pas"), "w", encoding="utf-8") as f:
                f.write(
                    "const ED_VERSION_SHOGO = 1247; ED_VERSION_AVP2 = 1249;\n"
                    "procedure ReadHeader; begin end;\n"
                    "procedure ReadSurface; begin\n"
                    "if m_Header.dwVersion = ED_VERSION_SHOGO then begin Read(fUScale, 4); Read(fVScale, 4); end\n"
                    "else m_Stream.Read(avOPQ[0], SizeOf(TLTVector) * 3);\n"
                    "Read(dwStickFlag, 4); Read(strTextureName, 1); Read(dwFlags, 4); Read(Shade, 3); end;\n"
                    "procedure ReadNodeContainers; begin end; procedure ReadNodeItem; begin end;\n"
                    "procedure ReadProperty; begin end;\n"
                )

            report = compiler_strategy.build_ltworldconverter_ed_writer_gap_report(
                ltworldconverter_root=lt_root,
                edunpacker_root=ed_root,
            )

        self.assertEqual(report.status, "requires_port")
        self.assertEqual(report.writer_version, 1247)
        self.assertEqual(report.reader_versions, (1247, 1249))
        self.assertTrue(any("five-float surface projection" in item for item in report.required_changes))
        self.assertTrue(any("OPQ vectors from decoded surface/poly UV data" in item for item in report.required_changes))
        self.assertTrue(any("Additive, Terrain, TimeOfDay" in item for item in report.required_changes))
        self.assertTrue(any("property record layout is reusable" in item for item in report.reusable_components))
        self.assertTrue(any("non-MM9 DAT layouts" in item for item in report.blockers))
        self.assertIn("LTWorldConverter ED writer v1249 port gap", compiler_strategy.format_ltworldconverter_ed_writer_gap_report(report))

    def test_source_world_comparison_pairs_same_stem_sources_with_v66_dats(self):
        with tempfile.TemporaryDirectory() as tmp:
            worlds = os.path.join(tmp, "WORLDS")
            sources = os.path.join(tmp, "sources")
            os.makedirs(worlds)
            os.makedirs(sources)
            self._write_versioned_file(os.path.join(worlds, "BOOTCAMP.DAT"), 66)
            self._write_versioned_file(os.path.join(worlds, "BOOTCAMP.ED"), 1249)
            self._write_versioned_file(os.path.join(worlds, "OTHER.DAT"), 85)
            self._write_versioned_file(os.path.join(sources, "OTHER.ED"), 1249)
            with open(os.path.join(sources, "LTALEVEL.lta"), "w", encoding="utf-8") as f:
                f.write("( world ( header ( versioncode 2 ) ) )")
            self._write_versioned_file(os.path.join(worlds, "LTALEVEL.DAT"), 66)
            self._write_versioned_file(os.path.join(sources, "COMPRESSED.ltc"), 0)

            report = compiler_strategy.build_source_world_comparison_report(
                worlds_dir=worlds,
                source_roots=[sources],
            )

        pairs = {pair.stem: pair for pair in report.pairs}
        self.assertEqual(report.dat_count, 3)
        self.assertEqual(report.v66_dat_count, 2)
        self.assertEqual(report.legacy_ed_count, 2)
        self.assertEqual(report.lta_count, 1)
        self.assertEqual(report.ltc_count, 1)
        self.assertEqual(report.paired_v66_dat_count, 2)
        self.assertEqual(pairs["BOOTCAMP"].status, "paired_v66_dat_with_source")
        self.assertEqual(pairs["LTALEVEL"].status, "paired_v66_dat_with_source")
        self.assertEqual(pairs["OTHER"].status, "source_with_non_v66_dat")
        self.assertEqual(pairs["COMPRESSED"].status, "source_without_dat")

    def test_formats_source_world_comparison_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_versioned_file(os.path.join(tmp, "A.DAT"), 66)
            self._write_versioned_file(os.path.join(tmp, "A.ED"), 1249)
            report = compiler_strategy.build_source_world_comparison_report(worlds_dir=tmp)

        text = compiler_strategy.format_source_world_comparison_report(report)

        self.assertIn("DAT source-world comparison corpus", text)
        self.assertIn("DAT=1 (v66=1)", text)
        self.assertIn("A: status=paired_v66_dat_with_source", text)

    def test_real_worlds_dir_reports_paired_legacy_ed_fixtures_when_available(self):
        worlds = os.path.join(ROOT, "mm9_data", "WORLDS")
        if not os.path.exists(os.path.join(worlds, "BOOTCAMP.DAT")):
            self.skipTest(f"missing test worlds dir: {worlds}")

        report = compiler_strategy.build_source_world_comparison_report(worlds_dir=worlds)

        self.assertGreaterEqual(report.v66_dat_count, 1)
        if os.path.exists(os.path.join(worlds, "BOOTCAMP.ED")):
            pairs = {pair.stem: pair for pair in report.pairs}
            self.assertEqual(pairs["BOOTCAMP"].status, "paired_v66_dat_with_source")

    def test_source_output_semantic_report_compares_source_geometry_with_dat_systems(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            worlds = os.path.join(tmp, "WORLDS")
            sources = os.path.join(tmp, "sources")
            os.makedirs(worlds)
            os.makedirs(sources)
            shutil.copyfile(bootcamp, os.path.join(worlds, "BOOTCAMP.DAT"))
            with open(os.path.join(sources, "BOOTCAMP.lta"), "w", encoding="utf-8") as f:
                f.write(self._minimal_lta())

            report = compiler_strategy.build_source_output_semantic_report(
                worlds_dir=worlds,
                source_roots=[sources],
                stems=["BOOTCAMP"],
            )

        self.assertEqual(report.paired_fixture_count, 1)
        self.assertEqual(report.compared_source_count, 1)
        self.assertEqual(report.comparable_source_count, 1)
        comparison = report.comparisons[0]
        self.assertEqual(comparison.status, "compared_with_compiled_only_gaps")
        self.assertEqual(comparison.source.status, "loaded")
        self.assertEqual(comparison.source.polygon_count, 1)
        self.assertGreater(comparison.dat.world_model_count, 0)
        self.assertGreater(comparison.dat.terrain_polygon_count, 0)
        self.assertGreater(comparison.dat.object_count or 0, 0)
        systems = {item.system: item for item in comparison.systems}
        self.assertEqual(systems["geometry"].status, "source_and_compiled_available")
        self.assertEqual(systems["objects"].status, "compiled_only")
        self.assertEqual(systems["physics"].status, "compiled_only")
        self.assertEqual(systems["render_data"].status, "compiled_only")
        self.assertTrue(any("compiled-only systems" in item for item in comparison.notes))

    def test_formats_source_output_semantic_report(self):
        source = compiler_strategy.SourceGeometrySummary(
            path="A.lta",
            format="lta",
            status="loaded",
            model_count=1,
            point_count=4,
            polygon_count=1,
            material_count=1,
        )
        dat = compiler_strategy.DatOutputSemanticSummary(
            path="A.DAT",
            status="loaded",
            version=66,
            world_model_count=2,
            terrain_model_count=1,
            terrain_polygon_count=10,
            object_count=3,
            render_data_size=128,
        )
        report = compiler_strategy.SourceOutputSemanticReport(
            comparisons=[
                compiler_strategy.SourceOutputSemanticComparison(
                    stem="A",
                    source_path="A.lta",
                    dat_path="A.DAT",
                    status="compared_with_compiled_only_gaps",
                    source=source,
                    dat=dat,
                    systems=(
                        compiler_strategy.SemanticSystemComparison(
                            "geometry",
                            "source_and_compiled_available",
                        ),
                    ),
                )
            ],
            paired_fixture_count=1,
            compared_source_count=1,
            comparable_source_count=1,
        )

        text = compiler_strategy.format_source_output_semantic_report(report)

        self.assertIn("DAT source-output semantic comparison", text)
        self.assertIn("A: status=compared_with_compiled_only_gaps", text)
        self.assertIn("geometry: source_and_compiled_available", text)

    def test_black_box_harness_runs_fake_processor_and_compares_reference_dat(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            source_ed = os.path.join(tmp, "BOOTCAMP.ED")
            fake_processor = os.path.join(tmp, "fake_processor.py")
            with open(source_ed, "wb") as f:
                f.write(struct.pack("<I", 1249))
                f.write(b"fixture")
            with open(fake_processor, "w", encoding="utf-8", newline="\n") as f:
                f.write(
                    "import os, sys\n"
                    "world = sys.argv[1]\n"
                    "ed = world if world.lower().endswith('.ed') else world + '.ed'\n"
                    "dat = os.path.splitext(ed)[0] + '.DAT'\n"
                    "with open(dat, 'rb') as inp:\n"
                    "    data = inp.read()\n"
                    "with open(dat, 'wb') as out:\n"
                    "    out.write(data)\n"
                    "log = os.path.join(os.path.dirname(ed), 'BOOTCAMP_0.log')\n"
                    "with open(log, 'w', encoding='utf-8') as out:\n"
                    "    out.write('Processing ' + ed + '\\n')\n"
                    "print('fake processor ok')\n"
                )
            work = os.path.join(tmp, "run")
            processor_project_dir = os.path.join(tmp, "resource_project")
            os.makedirs(processor_project_dir)

            report = compiler_strategy.run_black_box_ed_to_dat_harness(
                processor_path=sys.executable,
                processor_prefix_args=[fake_processor],
                source_ed_path=source_ed,
                reference_dat_path=bootcamp,
                work_dir=work,
                processor_project_dir=processor_project_dir,
            )

            self.assertEqual(report.status, "compiled_and_compared")
            self.assertEqual(report.returncode, 0)
            self.assertTrue(os.path.exists(report.stdout_path))
            self.assertTrue(report.log_paths)
            self.assertIsNotNone(report.reference)
            self.assertIsNotNone(report.generated)
            self.assertEqual(report.generated.version, 66)
            self.assertEqual(report.command[2], os.path.splitext(report.copied_ed_path)[0])
            self.assertIn("-logfile", report.command)
            self.assertNotIn("-skipdialog", report.command)
            project_arg = report.command.index("-projectdir")
            self.assertEqual(report.command[project_arg + 1], os.path.abspath(processor_project_dir))
            self.assertEqual(report.processor_project_dir, os.path.abspath(processor_project_dir))
            self.assertTrue(report.output_preseeded)
            self.assertTrue(report.output_rewritten)
            self.assertTrue(all(item.status == "match" for item in report.comparisons))
            text = compiler_strategy.format_black_box_compiler_harness_report(report)
            self.assertIn("DAT black-box compiler harness", text)
            self.assertIn("status: compiled_and_compared", text)

    def test_black_box_harness_rejects_unchanged_preseeded_reference_dat(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            source_ed = os.path.join(tmp, "BOOTCAMP.ED")
            noop_processor = os.path.join(tmp, "noop_processor.py")
            with open(source_ed, "wb") as f:
                f.write(struct.pack("<I", 1249))
                f.write(b"fixture")
            with open(noop_processor, "w", encoding="utf-8", newline="\n") as f:
                f.write(
                    "import os, sys\n"
                    "world = sys.argv[1]\n"
                    "ed = world if world.lower().endswith('.ed') else world + '.ed'\n"
                    "log = os.path.join(os.path.dirname(ed), 'BOOTCAMP_0.log')\n"
                    "with open(log, 'w', encoding='utf-8') as out:\n"
                    "    out.write('Processing ' + ed + '\\n')\n"
                    "print('fake processor no-op')\n"
                )

            report = compiler_strategy.run_black_box_ed_to_dat_harness(
                processor_path=sys.executable,
                processor_prefix_args=[noop_processor],
                source_ed_path=source_ed,
                reference_dat_path=bootcamp,
                work_dir=os.path.join(tmp, "run"),
            )

            self.assertEqual(report.status, "output_dat_unchanged")
            self.assertTrue(report.output_preseeded)
            self.assertFalse(report.output_rewritten)
            self.assertTrue(any("not rewritten" in item for item in report.notes))

    def test_surrogate_black_box_harness_generates_full_level_ed_then_runs_processor(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            fake_processor = os.path.join(tmp, "fake_processor.py")
            with open(fake_processor, "w", encoding="utf-8", newline="\n") as f:
                f.write(
                    "import os, sys\n"
                    "world = sys.argv[1]\n"
                    "ed = world if world.lower().endswith('.ed') else world + '.ed'\n"
                    "dat = os.path.splitext(ed)[0] + '.DAT'\n"
                    "with open(dat, 'rb') as inp:\n"
                    "    data = inp.read()\n"
                    "with open(dat, 'wb') as out:\n"
                    "    out.write(data)\n"
                    "with open(os.path.join(os.path.dirname(ed), 'BOOTCAMP_surrogate_0.log'), 'w', encoding='utf-8') as out:\n"
                    "    out.write('Processing ' + ed + '\\n')\n"
                    "print('fake surrogate processor ok')\n"
                )

            report = compiler_strategy.run_black_box_surrogate_ed_to_dat_harness(
                processor_path=sys.executable,
                processor_prefix_args=[fake_processor],
                source_dat_path=bootcamp,
                model_names=["MonsterDoor1"],
                work_dir=os.path.join(tmp, "run"),
            )

            self.assertEqual(report.status, "compiled_and_compared")
            self.assertEqual(report.surrogate_status, "full_level_surrogate_ed_built")
            self.assertEqual(report.surrogate_model_count, 1)
            self.assertEqual(report.surrogate_polygon_count, 6)
            self.assertEqual(report.surrogate_wrapper_kind, "zlib_blocked_full_level")
            self.assertTrue(os.path.exists(report.generated_ed_path))
            self.assertIsNotNone(report.harness)
            self.assertEqual(report.harness.status, "compiled_and_compared")
            self.assertTrue(report.harness.output_rewritten)
            self.assertIn("BOOTCAMP_surrogate", os.path.basename(report.harness.copied_ed_path))
            text = compiler_strategy.format_black_box_surrogate_compiler_harness_report(report)
            self.assertIn("DAT surrogate black-box compiler harness", text)
            self.assertIn("surrogate: status=full_level_surrogate_ed_built", text)
            self.assertIn("black-box result: status=compiled_and_compared", text)

    def test_surrogate_black_box_harness_reports_surrogate_build_failure(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.run_black_box_surrogate_ed_to_dat_harness(
                processor_path=sys.executable,
                source_dat_path=bootcamp,
                model_names=["DefinitelyMissingModel"],
                work_dir=os.path.join(tmp, "run"),
            )

            self.assertEqual(report.status, "surrogate_ed_build_failed")
            self.assertEqual(report.surrogate_status, "no_models_selected")
            self.assertIsNone(report.harness)
            self.assertTrue(any("no DAT world models matched" in item for item in report.notes))

    def test_prefab_surrogate_acceptance_report_prepares_manual_dedit_test(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_prefab_surrogate_acceptance_report(
                source_dat_path=bootcamp,
                model_names=["MonsterDoor1"],
                work_dir=os.path.join(tmp, "run"),
                prefab_install_dir=os.path.join(tmp, "PreFabs"),
            )

            self.assertEqual(report.status, "ready_for_manual_prefab_test")
            self.assertEqual(report.surrogate_status, "prefab_surrogate_ed_built")
            self.assertEqual(report.surrogate_model_count, 1)
            self.assertEqual(report.surrogate_polygon_count, 6)
            self.assertEqual(report.surrogate_object_count, 1)
            self.assertEqual(report.generated_brush_count, 1)
            self.assertEqual(report.generated_polygon_count, 6)
            self.assertEqual(report.generated_object_count, 1)
            self.assertEqual(report.generated_object_class_counts, {"Brush": 1})
            self.assertTrue(os.path.exists(report.generated_ed_path))
            self.assertTrue(report.prefab_install_path.endswith("BOOTCAMP_surrogate_prefab.ed"))
            self.assertTrue(any("prefab browser" in step for step in report.manual_steps))
            text = compiler_strategy.format_prefab_surrogate_acceptance_report(report)
            self.assertIn("DAT surrogate prefab acceptance harness", text)
            self.assertIn("status: ready_for_manual_prefab_test", text)
            self.assertIn("Brush=1", text)
            self.assertIn("manual step", text)

    def test_prefab_surrogate_acceptance_report_reports_build_failure(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_prefab_surrogate_acceptance_report(
                source_dat_path=bootcamp,
                model_names=["DefinitelyMissingModel"],
                work_dir=os.path.join(tmp, "run"),
            )

            self.assertEqual(report.status, "prefab_surrogate_build_failed")
            self.assertEqual(report.surrogate_status, "no_models_selected")
            self.assertEqual(report.generated_object_count, 0)
            self.assertTrue(report.blockers)
            self.assertTrue(any("no DAT world models matched" in item for item in report.notes))

    def test_prefab_surrogate_acceptance_corpus_report_generates_ready_batch(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_prefab_surrogate_acceptance_corpus_report(
                source_dat_path=bootcamp,
                model_names=["MonsterDoor1"],
                work_dir=os.path.join(tmp, "run"),
                prefab_install_dir=os.path.join(tmp, "PreFabs"),
            )

            self.assertEqual(report.status, "ready_for_manual_prefab_corpus_test")
            self.assertEqual(report.candidate_count, 1)
            self.assertEqual(report.generated_count, 1)
            self.assertEqual(report.ready_count, 1)
            self.assertEqual(report.failed_count, 0)
            self.assertEqual(report.candidates[0].model_name, "MonsterDoor1")
            self.assertEqual(report.candidates[0].status, "ready_for_manual_prefab_test")
            self.assertTrue(os.path.exists(report.candidates[0].generated_ed_path))
            self.assertIn("MonsterDoor1", os.path.basename(report.candidates[0].generated_ed_path))
            text = compiler_strategy.format_prefab_surrogate_acceptance_corpus_report(report)
            self.assertIn("DAT surrogate prefab acceptance corpus", text)
            self.assertIn("ready=1", text)
            self.assertIn("MonsterDoor1", text)

    def test_prefab_surrogate_acceptance_corpus_report_reports_missing_selection(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_prefab_surrogate_acceptance_corpus_report(
                source_dat_path=bootcamp,
                model_names=["DefinitelyMissingModel"],
                work_dir=os.path.join(tmp, "run"),
            )

            self.assertEqual(report.status, "no_models_selected")
            self.assertEqual(report.ready_count, 0)
            self.assertTrue(report.blockers)
            self.assertTrue(any("requested model was not found" in item for item in report.notes))

    def test_prefab_surrogate_composite_acceptance_report_generates_multibrush_prefab(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_prefab_surrogate_composite_acceptance_report(
                source_dat_path=bootcamp,
                model_names=["StoreDoorLeft", "StoreDoorRight"],
                work_dir=os.path.join(tmp, "run"),
                prefab_install_dir=os.path.join(tmp, "PreFabs"),
                output_prefix="BOOTCAMP_test",
            )

            self.assertEqual(report.status, "ready_for_manual_composite_prefab_test")
            self.assertEqual(report.model_count, 2)
            self.assertEqual(report.object_count, 2)
            self.assertEqual(report.generated_object_class_counts, {"Brush": 2})
            self.assertEqual(report.selected_model_names, ("StoreDoorLeft", "StoreDoorRight"))
            self.assertTrue(os.path.exists(report.generated_ed_path))
            self.assertTrue(report.prefab_install_path.endswith("BOOTCAMP_test_composite_surrogate_prefab.ed"))
            self.assertTrue(any("relative offsets" in step for step in report.manual_steps))
            self.assertTrue(report.acceptance is not None)
            text = compiler_strategy.format_prefab_surrogate_composite_acceptance_report(report)
            self.assertIn("DAT surrogate composite prefab acceptance", text)
            self.assertIn("status: ready_for_manual_composite_prefab_test", text)
            self.assertIn("Brush=2", text)
            self.assertIn("StoreDoorLeft", text)

    def test_prefab_surrogate_composite_acceptance_report_requires_two_models(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        report = compiler_strategy.build_prefab_surrogate_composite_acceptance_report(
            source_dat_path=bootcamp,
            model_names=["MonsterDoor1"],
        )

        self.assertEqual(report.status, "needs_multiple_models")
        self.assertTrue(report.blockers)

    def test_prefab_surrogate_composite_acceptance_report_generates_three_brush_prefab(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_prefab_surrogate_composite_acceptance_report(
                source_dat_path=bootcamp,
                model_names=["MonsterDoor1", "MonsterDoor2", "MuseumDoor0"],
                work_dir=os.path.join(tmp, "run"),
                prefab_install_dir=os.path.join(tmp, "PreFabs"),
                output_prefix="BOOTCAMP_group3",
            )

            self.assertEqual(report.status, "ready_for_manual_composite_prefab_test")
            self.assertEqual(report.model_count, 3)
            self.assertEqual(report.object_count, 3)
            self.assertEqual(report.generated_object_class_counts, {"Brush": 3})
            self.assertTrue(os.path.exists(report.generated_ed_path))
            self.assertTrue(any("direct-root pattern" in item for item in report.cautions))
            text = compiler_strategy.format_prefab_surrogate_composite_acceptance_report(report)
            self.assertIn("Brush=3", text)
            self.assertIn("MonsterDoor1", text)

    def test_prefab_surrogate_named_group_acceptance_report_generates_grouped_prefab(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_prefab_surrogate_named_group_acceptance_report(
                source_dat_path=bootcamp,
                model_names=[
                    "MonsterDoor1",
                    "MonsterDoor2",
                    "MuseumDoor0",
                    "MuseumDoor1",
                    "StoreDoorLeft",
                ],
                group_name="Bench",
                work_dir=os.path.join(tmp, "run"),
                prefab_install_dir=os.path.join(tmp, "PreFabs"),
                output_prefix="BOOTCAMP_grouped",
            )

            self.assertEqual(report.status, "ready_for_manual_named_group_prefab_test")
            self.assertEqual(report.hierarchy_kind, "named_group")
            self.assertEqual(report.group_name, "Bench")
            self.assertEqual(report.model_count, 5)
            self.assertEqual(report.object_count, 5)
            self.assertEqual(report.generated_object_class_counts, {"Brush": 5})
            self.assertTrue(os.path.exists(report.generated_ed_path))
            self.assertTrue(report.prefab_install_path.endswith("BOOTCAMP_grouped_named_group_surrogate_prefab.ed"))
            self.assertTrue(any("null/group-node pattern" in item for item in report.cautions))
            text = compiler_strategy.format_prefab_surrogate_composite_acceptance_report(report)
            self.assertIn("status: ready_for_manual_named_group_prefab_test", text)
            self.assertIn("hierarchy: named_group, group=Bench", text)
            self.assertIn("Brush=5", text)

    def test_prefab_surrogate_composite_acceptance_corpus_report_generates_ready_groups(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_prefab_surrogate_composite_acceptance_corpus_report(
                source_dat_path=bootcamp,
                model_groups=[
                    ["StoreDoorLeft", "StoreDoorRight"],
                    ["MonsterDoor1", "MonsterDoor2", "MuseumDoor0"],
                ],
                group_names=["store_pair", "door_group3"],
                work_dir=os.path.join(tmp, "run"),
                prefab_install_dir=os.path.join(tmp, "PreFabs"),
                output_prefix="BOOTCAMP_corpus",
            )

            self.assertEqual(report.status, "ready_for_manual_composite_prefab_corpus_test")
            self.assertEqual(report.group_count, 2)
            self.assertEqual(report.generated_count, 2)
            self.assertEqual(report.ready_count, 2)
            self.assertEqual(report.failed_count, 0)
            self.assertEqual(report.skipped_count, 0)
            self.assertTrue(all(os.path.exists(item.generated_ed_path) for item in report.candidates))
            self.assertEqual(report.candidates[0].group_name, "store_pair")
            self.assertEqual(report.candidates[1].model_count, 3)
            text = compiler_strategy.format_prefab_surrogate_composite_acceptance_corpus_report(report)
            self.assertIn("DAT surrogate direct-root composite prefab corpus", text)
            self.assertIn("ready=2", text)
            self.assertIn("door_group3", text)

    def test_prefab_surrogate_named_group_corpus_report_generates_ready_groups(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_prefab_surrogate_named_group_corpus_report(
                source_dat_path=bootcamp,
                model_groups=[
                    ["StoreDoorLeft", "StoreDoorRight"],
                    ["MonsterDoor1", "MonsterDoor2", "MuseumDoor0"],
                ],
                group_names=["StorePairGroup", "DoorGroup3"],
                work_dir=os.path.join(tmp, "run"),
                prefab_install_dir=os.path.join(tmp, "PreFabs"),
                output_prefix="BOOTCAMP_named_corpus",
            )

            self.assertEqual(report.status, "ready_for_manual_named_group_prefab_corpus_test")
            self.assertEqual(report.hierarchy_kind, "named_group")
            self.assertEqual(report.group_count, 2)
            self.assertEqual(report.generated_count, 2)
            self.assertEqual(report.ready_count, 2)
            self.assertEqual(report.failed_count, 0)
            self.assertEqual(report.skipped_count, 0)
            self.assertTrue(all(os.path.exists(item.generated_ed_path) for item in report.candidates))
            self.assertEqual(report.candidates[0].hierarchy_kind, "named_group")
            self.assertEqual(report.candidates[0].group_name, "StorePairGroup")
            self.assertEqual(report.candidates[1].acceptance.group_name, "DoorGroup3")
            self.assertTrue(report.candidates[0].generated_ed_path.endswith("BOOTCAMP_named_corpus_StorePairGroup_named_group_surrogate_prefab.ed"))
            text = compiler_strategy.format_prefab_surrogate_composite_acceptance_corpus_report(report)
            self.assertIn("DAT surrogate named-group composite prefab corpus", text)
            self.assertIn("status: ready_for_manual_named_group_prefab_corpus_test", text)
            self.assertIn("hierarchy: named_group", text)
            self.assertIn("StorePairGroup", text)
            self.assertIn("ready=2", text)

    def test_prefab_surrogate_named_group_pack_report_stages_manifest(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_prefab_surrogate_named_group_pack_report(
                source_dat_path=bootcamp,
                model_groups=[
                    ["StoreDoorLeft", "StoreDoorRight"],
                    ["MonsterDoor1", "MonsterDoor2", "MuseumDoor0"],
                ],
                group_names=["StorePairGroup", "DoorGroup3"],
                work_dir=os.path.join(tmp, "pack"),
                output_prefix="BOOTCAMP_pack",
            )

            self.assertEqual(report.status, "ready_for_manual_named_group_pack_test")
            self.assertEqual(report.hierarchy_kind, "named_group")
            self.assertEqual(report.entry_count, 2)
            self.assertEqual(report.ready_count, 2)
            self.assertEqual(report.staged_count, 2)
            self.assertEqual(report.failed_count, 0)
            self.assertTrue(os.path.isdir(report.staging_prefab_dir))
            self.assertTrue(os.path.exists(report.manifest_path))
            self.assertTrue(all(os.path.exists(item.staged_prefab_path) for item in report.entries))
            self.assertEqual(report.entries[0].status, "ready_staged_named_group_prefab")
            self.assertTrue(report.entries[0].staged_prefab_path.endswith("BOOTCAMP_pack_StorePairGroup_named_group_surrogate_prefab.ed"))
            with open(report.manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(manifest["kind"], "mm9_surrogate_named_group_prefab_pack")
            self.assertEqual(manifest["status"], "ready_for_manual_named_group_pack_test")
            self.assertEqual(manifest["staged_count"], 2)
            self.assertEqual(manifest["entries"][0]["group_name"], "StorePairGroup")
            self.assertEqual(manifest["entries"][1]["model_names"], ["MonsterDoor1", "MonsterDoor2", "MuseumDoor0"])
            text = compiler_strategy.format_prefab_surrogate_pack_report(report)
            self.assertIn("DAT surrogate named-group prefab pack", text)
            self.assertIn("staged=2", text)
            self.assertIn("StorePairGroup", text)

    def test_full_world_skeleton_acceptance_report_generates_medium_static_world(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        models = [
            "WorldObject12",
            "WorldObject13",
            "WorldObject14",
            "WorldObject15",
            "WorldObject4",
            "WorldObject5",
            "WorldObject16",
            "WorldObject7",
            "WorldObject17",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_full_world_skeleton_acceptance_report(
                source_dat_path=bootcamp,
                model_names=models,
                group_name="GeneratedUpperStaticCluster",
                work_dir=os.path.join(tmp, "run"),
                worlds_install_dir=os.path.join(tmp, "WORLDS"),
                output_filename="BOOTCAMP_upper_static_cluster_v10.ed",
            )

            self.assertEqual(report.status, "ready_for_manual_full_world_skeleton_test")
            self.assertEqual(report.group_name, "GeneratedUpperStaticCluster")
            self.assertEqual(report.selected_model_names, tuple(models))
            self.assertEqual(report.model_count, 9)
            self.assertEqual(report.point_count, 333)
            self.assertEqual(report.polygon_count, 216)
            self.assertEqual(report.object_count, 12)
            self.assertEqual(report.object_property_count, 314)
            self.assertEqual(report.wrapper_kind, "zlib_blocked_full_world_skeleton")
            self.assertEqual(report.generated_object_class_counts["Brush"], 9)
            self.assertEqual(report.generated_object_class_counts["WorldProperties"], 1)
            self.assertEqual(report.generated_object_class_counts["StartPoint"], 1)
            self.assertEqual(report.generated_object_class_counts["Light"], 1)
            self.assertTrue(os.path.exists(report.generated_ed_path))
            self.assertTrue(report.world_install_path.endswith("BOOTCAMP_upper_static_cluster_v10.ed"))
            self.assertTrue(any("Processor.exe" in step for step in report.manual_steps))
            text = compiler_strategy.format_full_world_skeleton_acceptance_report(report)
            self.assertIn("DAT surrogate full-world skeleton acceptance", text)
            self.assertIn("status: ready_for_manual_full_world_skeleton_test", text)
            self.assertIn("WorldObject16", text)
            self.assertIn("Brush=9", text)

    def test_full_world_skeleton_acceptance_report_can_include_validation_floor(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_full_world_skeleton_acceptance_report(
                source_dat_path=bootcamp,
                model_names=["MonsterDoor1"],
                group_name="GeneratedDoorWithFloor",
                work_dir=os.path.join(tmp, "run"),
                output_filename="door_with_floor.ed",
                include_validation_floor=True,
            )

            self.assertEqual(report.status, "ready_for_manual_full_world_skeleton_test")
            self.assertTrue(report.include_validation_floor)
            self.assertEqual(report.selected_model_names, ("MonsterDoor1",))
            self.assertEqual(report.model_count, 2)
            self.assertEqual(report.point_count, 16)
            self.assertEqual(report.polygon_count, 12)
            self.assertEqual(report.object_count, 5)
            self.assertEqual(report.object_property_count, 118)
            self.assertEqual(report.generated_object_class_counts["Brush"], 2)
            self.assertTrue(os.path.exists(report.generated_ed_path))
            self.assertTrue(any("validation floor" in step for step in report.manual_steps))
            text = compiler_strategy.format_full_world_skeleton_acceptance_report(report)
            self.assertIn("validation floor: included", text)
            self.assertIn("Brush=2", text)
            self.assertIn("ValidationFloor", text)

    def test_full_world_skeleton_acceptance_manifest_records_artifacts_and_manual_slots(self):
        report = compiler_strategy.FullWorldSkeletonAcceptanceReport(
            status="ready_for_manual_full_world_skeleton_test",
            source_dat_path="C:/tmp/source/DOOKSCASTLE.DAT",
            generated_ed_path="C:/tmp/out/full_world_skeleton_source/DOOKSCASTLE.ed",
            work_dir="C:/tmp/out",
            world_install_path="C:/game/data/WORLDS/DOOKSCASTLE.ed",
            group_name="DOOKSCASTLE_ReconstructedDAT",
            include_terrain_support_patch=True,
            selected_model_names=("WorldObject1", "WorldObject2"),
            model_count=3,
            point_count=24,
            polygon_count=18,
            object_count=6,
            object_property_count=144,
            generated_byte_count=1234,
            node_hierarchy_byte_count=456,
            wrapper_kind="zlib_blocked_full_world_skeleton",
            wrapper_block_count=1,
            generated_object_class_counts={"Brush": 3, "StartPoint": 1},
            models=(
                compiler_strategy.PrefabSurrogateCompositeModelSummary(
                    name="WorldObject1",
                    point_count=8,
                    polygon_count=6,
                    texture_count=1,
                    bounds_min=(0.0, 1.0, 2.0),
                    bounds_max=(3.0, 4.0, 5.0),
                    center=(1.5, 2.5, 3.5),
                    notes=("kept",),
                ),
            ),
            terrain_cutout_coverage_manifest_path="C:/tmp/out/cutouts.json",
            terrain_support_source_coverage_manifest_path="C:/tmp/out/source_coverage.json",
            manual_steps=("open in DEDit", "compile with Processor"),
            cautions=("compiled polygons are not original brushes",),
            notes=("generated for manual testing",),
        )

        manifest = compiler_strategy.build_full_world_skeleton_acceptance_manifest(
            report,
            original_source="WORLDS/DOOKSCASTLE.DAT",
            staged_source_dat_path="C:/tmp/out/source_dat/DOOKSCASTLE.DAT",
            text_report_path="C:/tmp/out/DOOKSCASTLE_dat_to_ed_report.txt",
            selection_report_path="C:/tmp/out/DOOKSCASTLE_dat_to_ed_selection_report.json",
            processor_log_paths=("C:/tmp/out/DOOKSCASTLE_0.log",),
            manual_status={"dedit_opened": True, "game_loaded": True},
            manual_notes="Loaded in game; geometry incomplete.",
        )

        self.assertEqual(manifest["kind"], "mm9_dat_to_ed_acceptance")
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["source"]["original_source"], "WORLDS/DOOKSCASTLE.DAT")
        self.assertEqual(
            manifest["artifacts"]["terrain_cutout_coverage_manifest_path"],
            "C:/tmp/out/cutouts.json",
        )
        self.assertEqual(
            manifest["artifacts"]["selection_report_path"],
            "C:/tmp/out/DOOKSCASTLE_dat_to_ed_selection_report.json",
        )
        self.assertEqual(manifest["generation"]["selected_model_count"], 2)
        self.assertEqual(manifest["generation"]["generated_object_class_counts"]["Brush"], 3)
        self.assertEqual(manifest["generation"]["models"][0]["bounds_min"], [0.0, 1.0, 2.0])
        self.assertTrue(manifest["manual_validation"]["dedit_opened"])
        self.assertIsNone(manifest["manual_validation"]["processor_compiled"])
        self.assertEqual(
            manifest["manual_validation"]["notes"],
            "Loaded in game; geometry incomplete.",
        )

    def test_dat_to_ed_selection_report_explains_selected_and_excluded_bootcamp_models(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        report = compiler_strategy.build_dat_to_ed_selection_report(
            source_dat_path=bootcamp,
            requested_model_names=("MonsterDoor1", "WorldObject2", "MissingModel"),
            selected_model_names=("MonsterDoor1",),
            include_terrain_support_patch=True,
            terrain_support_model_name="Terrain0",
            max_model_points=4096,
            max_model_polygons=4096,
        )
        by_name = {item.name: item for item in report.models}

        self.assertEqual(report.status, "selection_report_built")
        self.assertEqual(report.selected_model_count, 1)
        self.assertEqual(by_name["MonsterDoor1"].status, "selected")
        self.assertEqual(by_name["Terrain0"].status, "terrain_support_source")
        self.assertEqual(by_name["PhysicsBSP"].status, "excluded_system")
        if "VisBSP" in by_name:
            self.assertEqual(by_name["VisBSP"].status, "excluded_system")
        self.assertEqual(by_name["WorldObject2"].status, "excluded_filtered")
        rail_helpers = [
            item for item in report.models
            if item.status == "excluded_helper_texture"
            and any("aiRail" in reason for reason in item.reasons)
        ]
        self.assertGreater(len(rail_helpers), 0)
        self.assertGreater(
            report.helper_only_exclusions_by_role["aiRail"]["model_count"],
            0,
        )
        self.assertGreater(
            report.helper_only_exclusions_by_role["aiRail"]["polygon_count"],
            0,
        )
        self.assertIn("collision", report.helper_only_exclusions_by_role)
        self.assertIn("skyVisibility", report.helper_only_exclusions_by_role)
        self.assertGreater(report.status_counts["excluded_not_requested"], 0)

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "selection.json")
            compiler_strategy.write_dat_to_ed_selection_report(report, out)
            with open(out, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        self.assertEqual(manifest["kind"], "mm9_dat_to_ed_selection_report")
        self.assertEqual(manifest["summary"]["selected_model_count"], 1)
        self.assertEqual(manifest["models"][0]["index"], 0)
        self.assertIn("status_counts", manifest["summary"])
        self.assertIn("helper_only_exclusions_by_role", manifest["summary"])
        self.assertIn("helper_roles", manifest["models"][0])

    def test_dat_to_ed_selection_report_marks_physics_shell_source_when_enabled(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        report = compiler_strategy.build_dat_to_ed_selection_report(
            source_dat_path=bootcamp,
            requested_model_names=("MonsterDoor1",),
            selected_model_names=("MonsterDoor1",),
            include_physics_shell_patch=True,
            physics_shell_model_name="PhysicsBSP",
            max_model_points=4096,
            max_model_polygons=4096,
        )
        by_name = {item.name: item for item in report.models}

        self.assertEqual(report.status, "selection_report_built")
        self.assertEqual(report.selected_model_count, 1)
        self.assertEqual(report.physics_shell_source_count, 1)
        self.assertEqual(report.excluded_model_count, report.total_model_count - 2)
        self.assertEqual(by_name["MonsterDoor1"].status, "selected")
        self.assertEqual(by_name["PhysicsBSP"].status, "physics_shell_source")
        self.assertTrue(any("static-shell" in reason for reason in by_name["PhysicsBSP"].reasons))

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "selection.json")
            compiler_strategy.write_dat_to_ed_selection_report(report, out)
            with open(out, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        self.assertTrue(manifest["include_physics_shell_patch"])
        self.assertEqual(manifest["physics_shell_model_name"], "PhysicsBSP")
        self.assertEqual(manifest["summary"]["physics_shell_source_count"], 1)

    def test_anskramkeep_no_helper_selection_baseline(self):
        anskramkeep = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.DAT")
        if not os.path.exists(anskramkeep):
            self.skipTest(f"missing test level: {anskramkeep}")

        with open(anskramkeep, "rb") as f:
            parsed = bsp.parse(f.read())
        selected_names = terrain_semantics.default_dat_to_ed_model_names(parsed)
        report = compiler_strategy.build_dat_to_ed_selection_report(
            source_dat_path=anskramkeep,
            requested_model_names=selected_names,
            selected_model_names=selected_names,
            include_physics_shell_patch=True,
            physics_shell_model_name="PhysicsBSP",
            max_models=512,
            max_model_points=16384,
            max_model_polygons=16384,
            max_total_points=65536,
            max_total_polygons=65536,
        )
        helper_only_names = tuple(
            item.name for item in report.models
            if item.status == "excluded_helper_texture"
        )

        self.assertEqual(report.status, "selection_report_built")
        self.assertEqual(report.selected_model_count, 106)
        self.assertEqual(report.physics_shell_source_count, 1)
        self.assertEqual(report.status_counts["excluded_helper_texture"], 256)
        self.assertEqual(
            hashlib.sha256("\n".join(report.selected_model_names).encode("utf-8")).hexdigest(),
            "398d560d33b9b76afeb1da03f7d32ab834bd6ac6675f65ea801834d62d75ca7a",
        )
        self.assertEqual(
            hashlib.sha256("\n".join(helper_only_names).encode("utf-8")).hexdigest(),
            "8c384e3b6cf647c850d38c363ab0b4d1c6557847eff742fa13b47db5f72b942e",
        )
        self.assertEqual(report.selected_model_names[:3], ("ExitStairs", "ShooterPlate8", "ShooterPlate7"))
        self.assertEqual(report.selected_model_names[-3:], ("CritterBreakOut1", "Carpet28", "Carpet29"))
        self.assertIn("AITrk2", helper_only_names)
        self.assertIn("InvisibleBrush7", helper_only_names)
        self.assertNotIn("AITrk2", report.selected_model_names)
        self.assertEqual(
            report.helper_only_exclusions_by_role["aiRail"],
            {"model_count": 230, "polygon_count": 1380},
        )
        self.assertEqual(
            report.helper_only_exclusions_by_role["collision"],
            {"model_count": 26, "polygon_count": 144},
        )
        self.assertEqual(
            report.helper_only_exclusions_by_role["sprite"],
            {"model_count": 12, "polygon_count": 12},
        )
        self.assertEqual(
            report.helper_only_exclusions_by_role["skyVisibility"],
            {"model_count": 0, "polygon_count": 0},
        )

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "selection.json")
            compiler_strategy.write_dat_to_ed_selection_report(report, out)
            with open(out, "r", encoding="utf-8") as f:
                manifest = json.load(f)

        self.assertEqual(
            manifest["summary"]["helper_only_exclusions_by_role"]["aiRail"]["model_count"],
            230,
        )
        self.assertEqual(
            manifest["summary"]["helper_only_exclusions_by_role"]["collision"]["polygon_count"],
            144,
        )
        physics = next(item for item in manifest["models"] if item["name"] == "PhysicsBSP")
        self.assertEqual(physics["status"], "physics_shell_source")

    def test_anskramkeep_no_helper_processor_log_baseline_when_available(self):
        log_path = os.path.join(
            "C:\\",
            "lithtech",
            "Lith21tools",
            "ANSKRAMKEEP_reconstructed_physics_shell_no_helpers_1.log",
        )
        if not os.path.exists(log_path):
            self.skipTest(f"missing local Processor log: {log_path}")

        summary = compiler_strategy.parse_processor_log_summary(log_path)

        self.assertEqual(summary.status, "loaded")
        self.assertTrue(summary.processing_path.endswith("ANSKRAMKEEP_reconstructed_physics_shell_no_helpers.ed"))
        self.assertEqual(
            summary.warning_counts["** Unable to generate a plane (0)"],
            4,
        )
        self.assertEqual(summary.input_polygon_count, 11721)
        self.assertEqual(summary.output_polygon_count, 4470)
        self.assertEqual(summary.tree_depth, 52)
        self.assertEqual(summary.unseen_removed_polygon_count, 58)
        self.assertAlmostEqual(summary.runtime_minutes, 0.92)
        self.assertEqual(summary.problem_brush_count, 674)
        self.assertEqual(
            summary.model_polygon_counts,
            (("PhysicsBSP", 4470), ("VisBSP", 4470)),
        )

    def test_full_world_skeleton_acceptance_report_generates_multi_cluster_world(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        models = [
            "WorldObject12",
            "WorldObject13",
            "WorldObject14",
            "WorldObject15",
            "WorldObject4",
            "WorldObject5",
            "WorldObject16",
            "WorldObject7",
            "WorldObject17",
            "WorldObject18",
            "WorldObject19",
            "WorldObject20",
            "WorldObject21",
            "WorldObject22",
            "WorldObject23",
            "WorldObject24",
            "WorldObject25",
            "WorldObject26",
            "WorldObject28",
            "WorldObject29",
            "WorldObject30",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_full_world_skeleton_acceptance_report(
                source_dat_path=bootcamp,
                model_names=models,
                group_name="GeneratedTwinStaticClusters",
                work_dir=os.path.join(tmp, "run"),
                output_filename="twin_static_clusters_v12.ed",
                include_validation_floor=True,
                validation_floor_name="ValidationFloor_TwinStaticClusters",
            )

            self.assertEqual(report.status, "ready_for_manual_full_world_skeleton_test")
            self.assertTrue(report.include_validation_floor)
            self.assertEqual(report.selected_model_names, tuple(models))
            self.assertEqual(report.model_count, 22)
            self.assertEqual(report.point_count, 830)
            self.assertEqual(report.polygon_count, 498)
            self.assertEqual(report.object_count, 25)
            self.assertEqual(report.object_property_count, 678)
            self.assertEqual(report.generated_object_class_counts["Brush"], 22)
            self.assertTrue(os.path.exists(report.generated_ed_path))
            text = compiler_strategy.format_full_world_skeleton_acceptance_report(report)
            self.assertIn("GeneratedTwinStaticClusters", text)
            self.assertIn("Brush=22", text)
            self.assertIn("ValidationFloor_TwinStaticClusters", text)

    def test_full_world_skeleton_acceptance_report_generates_terrain_backed_world(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        models = [
            "WorldObject12",
            "WorldObject13",
            "WorldObject14",
            "WorldObject15",
            "WorldObject4",
            "WorldObject5",
            "WorldObject16",
            "WorldObject7",
            "WorldObject17",
            "WorldObject18",
            "WorldObject19",
            "WorldObject20",
            "WorldObject21",
            "WorldObject22",
            "WorldObject23",
            "WorldObject24",
            "WorldObject25",
            "WorldObject26",
            "WorldObject28",
            "WorldObject29",
            "WorldObject30",
            "Terrain0",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_full_world_skeleton_acceptance_report(
                source_dat_path=bootcamp,
                model_names=models,
                group_name="GeneratedTerrainBackedTwinClusters",
                work_dir=os.path.join(tmp, "run"),
                output_filename="terrain_backed_twin_clusters_v13.ed",
                max_models=32,
                max_model_points=4096,
                max_model_polygons=8192,
                max_total_points=8192,
                max_total_polygons=8192,
            )

            self.assertEqual(report.status, "ready_for_manual_full_world_skeleton_test")
            self.assertFalse(report.include_validation_floor)
            self.assertEqual(report.selected_model_names, tuple(models))
            self.assertEqual(report.model_count, 22)
            self.assertEqual(report.point_count, 3402)
            self.assertEqual(report.polygon_count, 4572)
            self.assertEqual(report.object_count, 25)
            self.assertEqual(report.object_property_count, 678)
            self.assertEqual(report.wrapper_block_count, 12)
            self.assertEqual(report.generated_object_class_counts["Brush"], 22)
            self.assertTrue(os.path.exists(report.generated_ed_path))
            self.assertTrue(any("Terrain*" in item for item in report.cautions))
            text = compiler_strategy.format_full_world_skeleton_acceptance_report(report)
            self.assertIn("GeneratedTerrainBackedTwinClusters", text)
            self.assertIn("Terrain0", text)
            self.assertIn("polygons=4080", text)

    def test_full_world_skeleton_acceptance_report_generates_closed_terrain_support_patch(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        models = [
            "WorldObject12",
            "WorldObject13",
            "WorldObject14",
            "WorldObject15",
            "WorldObject4",
            "WorldObject5",
            "WorldObject16",
            "WorldObject7",
            "WorldObject17",
            "WorldObject18",
            "WorldObject19",
            "WorldObject20",
            "WorldObject21",
            "WorldObject22",
            "WorldObject23",
            "WorldObject24",
            "WorldObject25",
            "WorldObject26",
            "WorldObject28",
            "WorldObject29",
            "WorldObject30",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_full_world_skeleton_acceptance_report(
                source_dat_path=bootcamp,
                model_names=models,
                group_name="GeneratedTerrainPatchTwinClusters",
                work_dir=os.path.join(tmp, "run"),
                output_filename="terrain_patch_twin_clusters_v14.ed",
                include_terrain_support_patch=True,
                terrain_support_name_prefix="TerrainPatchTwinClusters",
                terrain_support_margin=0.0,
                terrain_support_max_polygons=96,
                terrain_support_thickness=128.0,
                include_terrain_support_source_coverage=True,
                terrain_support_source_coverage_sample_grid=1,
                max_models=128,
                max_total_points=4096,
                max_total_polygons=4096,
            )

            self.assertEqual(report.status, "ready_for_manual_full_world_skeleton_test")
            self.assertFalse(report.include_validation_floor)
            self.assertTrue(report.include_terrain_support_patch)
            self.assertEqual(report.selected_model_names, tuple(models))
            self.assertEqual(report.model_count, 68)
            self.assertEqual(report.point_count, 1104)
            self.assertEqual(report.polygon_count, 727)
            self.assertEqual(report.object_count, 71)
            self.assertEqual(report.object_property_count, 1966)
            self.assertEqual(report.generated_object_class_counts["Brush"], 68)
            self.assertTrue(os.path.exists(report.generated_ed_path))
            self.assertIsNotNone(report.terrain_cutout_coverage)
            self.assertTrue(os.path.exists(report.terrain_cutout_coverage_manifest_path))
            self.assertGreater(report.terrain_cutout_coverage.covered_cutout_count, 0)
            with open(report.terrain_cutout_coverage_manifest_path, "r", encoding="utf-8") as f:
                cutout_manifest = json.load(f)
            self.assertEqual(cutout_manifest["kind"], "mm9_terrain_cutout_coverage")
            self.assertGreater(cutout_manifest["covered_cutout_count"], 0)
            self.assertIsNotNone(report.terrain_support_source_coverage)
            self.assertTrue(os.path.exists(report.terrain_support_source_coverage_manifest_path))
            self.assertGreater(report.terrain_support_source_coverage.generated_coverage_polygon_count, 0)
            self.assertGreater(report.terrain_support_source_coverage.missing_sample_count, 0)
            with open(report.terrain_support_source_coverage_manifest_path, "r", encoding="utf-8") as f:
                source_coverage_manifest = json.load(f)
            self.assertEqual(source_coverage_manifest["kind"], "mm9_terrain_support_source_coverage")
            self.assertGreater(source_coverage_manifest["missing_sample_count"], 0)
            self.assertTrue(any("Terrain support patches" in item for item in report.cautions))
            self.assertTrue(any("StartPoint" in item and "support face" in item for item in report.notes))
            scan = legacy_ed.load_legacy_ed_object_scan_report(report.generated_ed_path)
            start_points = [record for record in scan.records if record.class_name == "StartPoint"]
            self.assertEqual(len(start_points), 1)
            self.assertEqual(start_points[0].property_value("MovePlayerToFloor"), True)
            start_pos = start_points[0].property_value("Pos")
            self.assertAlmostEqual(start_pos[0], 10407.0, places=2)
            self.assertAlmostEqual(start_pos[1], 817.5, places=2)
            self.assertAlmostEqual(start_pos[2], -3578.20, places=2)
            text = compiler_strategy.format_full_world_skeleton_acceptance_report(report)
            self.assertIn("terrain support patch: included", text)
            self.assertIn("terrain cutout coverage", text)
            self.assertIn("terrain support source coverage", text)
            self.assertIn("GeneratedTerrainPatchTwinClusters", text)
            self.assertIn("Brush=68", text)

    def test_full_world_skeleton_acceptance_report_blocks_processor_toxic_outputs(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        models = [
            "WorldObject12",
            "WorldObject13",
            "WorldObject14",
            "WorldObject15",
            "WorldObject4",
            "WorldObject5",
            "WorldObject16",
            "WorldObject7",
            "WorldObject17",
            "WorldObject18",
            "WorldObject19",
            "WorldObject20",
            "WorldObject21",
            "WorldObject22",
            "WorldObject23",
            "WorldObject24",
            "WorldObject25",
            "WorldObject26",
            "WorldObject28",
            "WorldObject29",
            "WorldObject30",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_full_world_skeleton_acceptance_report(
                source_dat_path=bootcamp,
                model_names=models,
                group_name="GeneratedTerrainPatchProcessorBudget",
                work_dir=os.path.join(tmp, "run"),
                output_filename="terrain_patch_processor_budget.ed",
                include_terrain_support_patch=True,
                terrain_support_name_prefix="TerrainPatchProcessorBudget",
                terrain_support_margin=0.0,
                terrain_support_max_polygons=96,
                terrain_support_thickness=128.0,
                max_processor_brushes=16,
                max_processor_polygons=128,
                max_models=128,
                max_total_points=4096,
                max_total_polygons=4096,
            )

            self.assertEqual(report.status, "full_world_skeleton_processor_budget_exceeded")
            self.assertEqual(report.model_count, 68)
            self.assertEqual(report.polygon_count, 727)
            self.assertEqual(report.max_processor_brushes, 16)
            self.assertEqual(report.max_processor_polygons, 128)
            self.assertTrue(os.path.exists(report.generated_ed_path))
            self.assertTrue(any("Processor budget" in item for item in report.blockers))
            self.assertTrue(any("Joining polies" in item for item in report.blockers))
            text = compiler_strategy.format_full_world_skeleton_acceptance_report(report)
            self.assertIn("Processor budget: brushes<=16, polygons<=128", text)

    def test_full_world_skeleton_acceptance_report_blocks_missing_physics_shell(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_full_world_skeleton_acceptance_report(
                source_dat_path=bootcamp,
                model_names=["MonsterDoor1"],
                group_name="GeneratedPhysicsShellMissing",
                work_dir=os.path.join(tmp, "run"),
                output_filename="physics_shell_missing.ed",
                block_unreconstructed_physics_shell=True,
                max_models=8,
                max_total_points=512,
                max_total_polygons=512,
            )

            self.assertEqual(report.status, "full_world_skeleton_static_shell_unreconstructed")
            self.assertTrue(os.path.exists(report.generated_ed_path))
            self.assertTrue(any("PhysicsBSP" in item for item in report.blockers))
            self.assertTrue(any("source Brush geometry" in item for item in report.blockers))
            text = compiler_strategy.format_full_world_skeleton_acceptance_report(report)
            self.assertIn("full_world_skeleton_static_shell_unreconstructed", text)
            self.assertIn("PhysicsBSP", text)

    def test_full_world_skeleton_acceptance_report_generates_budgeted_physics_shell_patch(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_full_world_skeleton_acceptance_report(
                source_dat_path=bootcamp,
                model_names=["MonsterDoor1"],
                group_name="GeneratedPhysicsShellProbe",
                work_dir=os.path.join(tmp, "run"),
                output_filename="physics_shell_probe.ed",
                include_physics_shell_patch=True,
                physics_shell_name_prefix="PhysicsShellProbe",
                physics_shell_max_polygons=4,
                physics_shell_thickness=16.0,
                block_unreconstructed_physics_shell=True,
                max_processor_brushes=16,
                max_processor_polygons=128,
                max_models=8,
                max_total_points=512,
                max_total_polygons=512,
            )

            self.assertEqual(report.status, "ready_for_manual_full_world_skeleton_test")
            self.assertTrue(report.include_physics_shell_patch)
            self.assertEqual(report.model_count, 5)
            self.assertEqual(report.point_count, 94)
            self.assertEqual(report.polygon_count, 57)
            self.assertEqual(report.object_count, 8)
            self.assertEqual(report.object_property_count, 202)
            self.assertEqual(report.generated_object_class_counts["Brush"], 5)
            self.assertTrue(os.path.exists(report.generated_ed_path))
            self.assertFalse(any("source Brush geometry" in item for item in report.blockers))
            self.assertTrue(any("PhysicsBSP shell patch" in item for item in report.cautions))
            self.assertTrue(any("shell slab brushes" in item for item in report.manual_steps))
            text = compiler_strategy.format_full_world_skeleton_acceptance_report(report)
            self.assertIn("PhysicsBSP shell patch: included", text)
            self.assertIn("Brush=5", text)

    def test_full_world_skeleton_acceptance_caps_physics_shell_by_generated_face_budget(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_full_world_skeleton_acceptance_report(
                source_dat_path=bootcamp,
                model_names=["MonsterDoor1"],
                group_name="GeneratedPhysicsShellCapped",
                work_dir=os.path.join(tmp, "run"),
                output_filename="physics_shell_capped.ed",
                include_physics_shell_patch=True,
                physics_shell_max_polygons=32,
                block_unreconstructed_physics_shell=True,
                max_processor_brushes=64,
                max_processor_polygons=64,
                max_models=8,
                max_total_points=4096,
                max_total_polygons=4096,
            )

            self.assertEqual(report.status, "ready_for_manual_full_world_skeleton_test")
            self.assertEqual(report.model_count, 5)
            self.assertEqual(report.polygon_count, 57)
            self.assertLessEqual(report.polygon_count, report.max_processor_polygons)
            self.assertTrue(any("32 -> 4" in item for item in report.notes))

    def test_terrain_cutout_coverage_report_identifies_building_footprints(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        report = compiler_strategy.build_terrain_cutout_coverage_report(
            source_dat_path=bootcamp,
            max_candidates=16,
        )

        self.assertEqual(report.status, "terrain_cutout_coverage_built")
        self.assertEqual(report.terrain_model_name, "Terrain0")
        self.assertGreater(report.terrain_coverage_polygon_count, 0)
        self.assertGreater(report.covered_cutout_count, 0)
        candidate_sets = {
            tuple(candidate.model_names): candidate
            for candidate in report.candidates
        }
        upper_building = candidate_sets[(
            "WorldObject12",
            "WorldObject13",
            "WorldObject14",
            "WorldObject15",
        )]
        lower_building = candidate_sets[(
            "WorldObject18",
            "WorldObject19",
            "WorldObject20",
            "WorldObject21",
        )]
        self.assertEqual(upper_building.classification, "covered_cutout")
        self.assertEqual(lower_building.classification, "covered_cutout")
        self.assertEqual(upper_building.missing_sample_count, upper_building.sample_count)
        self.assertEqual(lower_building.missing_sample_count, lower_building.sample_count)
        text = compiler_strategy.format_terrain_cutout_coverage_report(report)
        self.assertIn("DAT Terrain0 cutout coverage", text)
        self.assertIn("WorldObject12,WorldObject13,WorldObject14,WorldObject15", text)
        self.assertIn("covered_cutout", text)

    def test_full_world_skeleton_acceptance_report_blocks_oversized_selection(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_full_world_skeleton_acceptance_report(
                source_dat_path=bootcamp,
                model_names=["WorldObject12", "WorldObject13"],
                max_total_polygons=10,
                work_dir=os.path.join(tmp, "run"),
            )

            self.assertEqual(report.status, "full_world_skeleton_too_large")
            self.assertEqual(report.model_count, 2)
            self.assertEqual(report.polygon_count, 30)
            self.assertFalse(report.generated_ed_path)
            self.assertTrue(report.blockers)

    def test_prefab_surrogate_composite_acceptance_corpus_report_skips_missing_group(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            report = compiler_strategy.build_prefab_surrogate_composite_acceptance_corpus_report(
                source_dat_path=bootcamp,
                model_groups=[["MonsterDoor1", "DefinitelyMissingModel"]],
                group_names=["bad_group"],
                work_dir=os.path.join(tmp, "run"),
            )

            self.assertEqual(report.status, "no_eligible_groups")
            self.assertEqual(report.generated_count, 0)
            self.assertEqual(report.ready_count, 0)
            self.assertEqual(report.skipped_count, 1)
            self.assertEqual(report.candidates[0].status, "skipped_missing_models")
            self.assertTrue(any("missing model" in item for item in report.candidates[0].notes))

    def test_black_box_corpus_harness_runs_paired_legacy_ed_dat_fixtures(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            worlds = os.path.join(tmp, "WORLDS")
            os.makedirs(worlds)
            shutil.copyfile(bootcamp, os.path.join(worlds, "BOOTCAMP.DAT"))
            with open(os.path.join(worlds, "BOOTCAMP.ED"), "wb") as f:
                f.write(struct.pack("<I", 1249))
                f.write(b"fixture")
            fake_processor = os.path.join(tmp, "fake_processor.py")
            with open(fake_processor, "w", encoding="utf-8", newline="\n") as f:
                f.write(
                    "import os, sys\n"
                    "world = sys.argv[1]\n"
                    "ed = world if world.lower().endswith('.ed') else world + '.ed'\n"
                    "dat = os.path.splitext(ed)[0] + '.DAT'\n"
                    "with open(dat, 'rb') as inp:\n"
                    "    data = inp.read()\n"
                    "with open(dat, 'wb') as out:\n"
                    "    out.write(data)\n"
                    "with open(os.path.join(os.path.dirname(ed), 'BOOTCAMP_0.log'), 'w', encoding='utf-8') as out:\n"
                    "    out.write('Processing ' + ed + '\\n')\n"
                )

            report = compiler_strategy.run_black_box_ed_to_dat_corpus_harness(
                processor_path=sys.executable,
                processor_prefix_args=[fake_processor],
                worlds_dir=worlds,
                work_dir=os.path.join(tmp, "corpus"),
                stems=["BOOTCAMP"],
            )

            self.assertEqual(report.status, "all_matched")
            self.assertEqual(report.fixture_count, 1)
            self.assertEqual(report.ran_count, 1)
            self.assertEqual(report.matched_count, 1)
            self.assertEqual(report.failed_count, 0)
            self.assertEqual(report.runs[0].stem, "BOOTCAMP")
            self.assertTrue(report.runs[0].report.output_rewritten)
            text = compiler_strategy.format_black_box_compiler_corpus_report(report)
            self.assertIn("DAT black-box compiler corpus", text)
            self.assertIn("status: all_matched", text)
            self.assertIn("BOOTCAMP: status=compiled_and_compared", text)

    def test_captured_black_box_output_report_compares_existing_dat(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            generated = os.path.join(tmp, "BOOTCAMP.DAT")
            log_path = os.path.join(tmp, "BOOTCAMP_0.log")
            shutil.copyfile(bootcamp, generated)
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(
                    "Processing BOOTCAMP.ed\n"
                    "WorldTree nodes: 1061\n"
                    "WorldTree depth: 5\n"
                    "Lightmap Grid Size: 64.00\n"
                    "12 BTW poly splits.\n"
                    "4 polies removed.\n"
                    "8 polies left.\n"
                    "Found 3 problem brushes\n"
                    "Number of (unseen) polies removed: 2\n"
                    "Added 5 verts for T's\n"
                    "-  Terrain0 (4080 polies)\n"
                    "** Couldn't find any textures.\n"
                    "** Unable to generate a plane (0)\n"
                    "** Unable to generate a plane (0)\n"
                    "Number of output polies: 8540\n"
                    "Number of output vertices: 16678\n"
                    "Tree depth: 52\n"
                    "Done in 0.92 minutes\n"
                    "Number of objects: 591\n"
                )

            report = compiler_strategy.build_black_box_captured_output_report(
                generated_dat_path=generated,
                reference_dat_path=bootcamp,
                source_ed_path=os.path.join(tmp, "BOOTCAMP.ED"),
                processor_path=os.path.join(tmp, "Processor.exe"),
                log_paths=[log_path],
            )

            self.assertEqual(report.status, "captured_and_compared")
            self.assertTrue(report.captured_output)
            self.assertTrue(report.output_rewritten)
            self.assertFalse(report.output_preseeded)
            self.assertTrue(all(item.status == "match" for item in report.comparisons))
            self.assertTrue(report.world_model_comparisons)
            self.assertTrue(all(item.status == "match" for item in report.world_model_comparisons))
            self.assertEqual(len(report.processor_logs), 1)
            self.assertEqual(report.processor_logs[0].status, "loaded")
            self.assertEqual(report.processor_logs[0].processing_path, "BOOTCAMP.ed")
            self.assertEqual(report.processor_logs[0].world_tree_nodes, 1061)
            self.assertEqual(report.processor_logs[0].output_polygon_count, 8540)
            self.assertEqual(report.processor_logs[0].object_count, 591)
            self.assertEqual(report.processor_logs[0].btw_poly_split_count, 12)
            self.assertEqual(report.processor_logs[0].joined_removed_polygon_count, 4)
            self.assertEqual(report.processor_logs[0].joined_polygon_count, 8)
            self.assertEqual(report.processor_logs[0].problem_brush_count, 3)
            self.assertEqual(report.processor_logs[0].unseen_removed_polygon_count, 2)
            self.assertEqual(report.processor_logs[0].t_junction_vertex_count, 5)
            self.assertEqual(report.processor_logs[0].tree_depth, 52)
            self.assertAlmostEqual(report.processor_logs[0].runtime_minutes, 0.92)
            self.assertEqual(
                report.processor_logs[0].warning_counts["** Unable to generate a plane (0)"],
                2,
            )
            self.assertEqual(report.processor_logs[0].model_polygon_counts, (("Terrain0", 4080),))
            self.assertTrue(report.processor_logs[0].warnings)
            self.assertTrue(any("source ED was not found" in item for item in report.notes))
            text = compiler_strategy.format_black_box_compiler_harness_report(report)
        self.assertIn("captured output: true", text)
        self.assertIn("processor log summary", text)
        self.assertIn("problem_brushes=3", text)
        self.assertIn("tree_depth=52", text)
        self.assertIn("2 x ** Unable to generate a plane (0)", text)

    def test_captured_black_box_output_corpus_report_uses_same_stem_outputs(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            worlds = os.path.join(tmp, "WORLDS")
            generated_dir = os.path.join(tmp, "generated")
            os.makedirs(worlds)
            os.makedirs(generated_dir)
            shutil.copyfile(bootcamp, os.path.join(worlds, "BOOTCAMP.DAT"))
            shutil.copyfile(bootcamp, os.path.join(generated_dir, "BOOTCAMP.DAT"))
            with open(os.path.join(worlds, "BOOTCAMP.ED"), "wb") as f:
                f.write(struct.pack("<I", 1249))
                f.write(b"fixture")
            with open(os.path.join(generated_dir, "BOOTCAMP_0.log"), "w", encoding="utf-8") as f:
                f.write("Processing BOOTCAMP.ed\n")

            report = compiler_strategy.build_black_box_captured_output_corpus_report(
                worlds_dir=worlds,
                generated_dir=generated_dir,
                stems=["BOOTCAMP"],
            )

            self.assertEqual(report.status, "all_matched")
            self.assertEqual(report.matched_count, 1)
            self.assertEqual(report.runs[0].status, "captured_and_compared")
            self.assertTrue(report.runs[0].report.captured_output)
            self.assertTrue(report.runs[0].report.world_model_comparisons)
            text = compiler_strategy.format_black_box_compiler_corpus_report(report)
            self.assertIn("DAT black-box compiler corpus", text)
            self.assertIn("BOOTCAMP: status=captured_and_compared", text)
            self.assertIn("world_model_diffs=0", text)

    def test_world_model_summary_comparison_reports_model_mismatches(self):
        reference = compiler_strategy.DatOutputSemanticSummary(
            path="reference.DAT",
            status="loaded",
            world_model_summaries=(
                compiler_strategy.DatWorldModelSemanticSummary(
                    index=0,
                    name="Terrain0",
                    point_count=4,
                    polygon_count=1,
                    texture_count=1,
                    surface_count=1,
                    raw_size=128,
                ),
            ),
        )
        generated = compiler_strategy.DatOutputSemanticSummary(
            path="generated.DAT",
            status="loaded",
            world_model_summaries=(
                compiler_strategy.DatWorldModelSemanticSummary(
                    index=0,
                    name="Terrain0",
                    point_count=5,
                    polygon_count=2,
                    texture_count=1,
                    surface_count=1,
                    raw_size=160,
                ),
                compiler_strategy.DatWorldModelSemanticSummary(
                    index=1,
                    name="PhysicsBSP",
                    point_count=8,
                    polygon_count=6,
                    texture_count=1,
                    surface_count=6,
                    raw_size=256,
                ),
            ),
        )

        comparisons = compiler_strategy._compare_black_box_world_model_summaries(reference, generated)

        self.assertEqual(comparisons[0].status, "mismatch")
        self.assertEqual(comparisons[0].name, "Terrain0")
        self.assertIn("points=4", comparisons[0].reference_detail)
        self.assertIn("points=5", comparisons[0].generated_detail)
        self.assertEqual(comparisons[1].status, "extra_generated")

    def test_black_box_acceptance_accepts_validated_regenerated_system_drift(self):
        harness = self._acceptance_harness(
            status="captured_with_semantic_differences",
            mismatches=("terrain", "physics", "visibility", "render_data", "top_level_sections"),
            warning=True,
        )
        manual = compiler_strategy.BlackBoxCompilerManualValidation(
            status="passed",
            tested_at="2026-06-21",
            fresh_load=True,
            visuals_ok=True,
            collision_ok=True,
            notes=("BOOTCAMP loaded and walked in game",),
        )

        report = compiler_strategy.build_black_box_compiler_acceptance_report(
            harness,
            manual_validation=manual,
        )

        self.assertEqual(report.status, "accepted_with_validated_differences")
        self.assertEqual(report.unaccepted_differences, ())
        self.assertEqual(
            report.accepted_differences,
            ("terrain", "physics", "visibility", "render_data", "top_level_sections"),
        )
        self.assertTrue(report.cautions)
        text = compiler_strategy.format_black_box_compiler_acceptance_report(report)
        self.assertIn("accepted differences: terrain, physics, visibility, render_data, top_level_sections", text)
        self.assertIn("manual validation: status=passed", text)

    def test_black_box_acceptance_requires_manual_validation_for_regenerated_drift(self):
        harness = self._acceptance_harness(
            status="captured_with_semantic_differences",
            mismatches=("terrain",),
        )

        report = compiler_strategy.build_black_box_compiler_acceptance_report(harness)

        self.assertEqual(report.status, "needs_manual_validation")
        self.assertTrue(any("manual fresh-load" in item for item in report.cautions))

    def test_black_box_acceptance_blocks_unaccepted_stable_system_drift(self):
        harness = self._acceptance_harness(
            status="captured_with_semantic_differences",
            mismatches=("objects",),
        )
        manual = compiler_strategy.BlackBoxCompilerManualValidation(
            status="passed",
            fresh_load=True,
            visuals_ok=True,
            collision_ok=True,
        )

        report = compiler_strategy.build_black_box_compiler_acceptance_report(
            harness,
            manual_validation=manual,
        )

        self.assertEqual(report.status, "blocked_unaccepted_differences")
        self.assertEqual(report.unaccepted_differences, ("objects",))
        self.assertTrue(report.blockers)

    def test_compiled_validation_report_accepts_v16_terrain_support_floor_probe(self):
        ed_path = os.path.join(
            ROOT,
            "output",
            "full_world_skeleton_source",
            "bootcamp_terrain_patch_twin_clusters_v16.ed",
        )
        dat_path = os.path.join(
            "C:\\",
            "Program Files (x86)",
            "GOG Galaxy",
            "Games",
            "Might and Magic 9",
            "data",
            "WORLDS",
            "bootcamp_terrain_patch_twin_clusters_v16.dat",
        )
        log_path = os.path.join(
            "C:\\",
            "lithtech",
            "Lith21tools",
            "bootcamp_terrain_patch_twin_clusters_v16_0.log",
        )
        missing = [path for path in (ed_path, dat_path, log_path) if not os.path.exists(path)]
        if missing:
            self.skipTest("missing local v16 compiled terrain-support artifact(s): " + ", ".join(missing))

        manual = compiler_strategy.BlackBoxCompilerManualValidation(
            status="passed",
            tested_at="2026-06-24",
            fresh_load=True,
            visuals_ok=True,
            collision_ok=True,
            notes=("v16 terrain support rock was visible and walkable in game",),
        )
        report = compiler_strategy.build_full_world_skeleton_compiled_validation_report(
            generated_ed_path=ed_path,
            compiled_dat_path=dat_path,
            processor_log_paths=[log_path],
            manual_validation=manual,
        )

        self.assertEqual(report.status, "validated_in_game")
        self.assertAlmostEqual(report.start_point[0], 10407.0, places=2)
        self.assertAlmostEqual(report.physics_floor_y, 681.70, places=2)
        self.assertLess(report.physics_floor_drop, 256.0)
        self.assertEqual(report.dat.physics_polygon_count, 246)
        self.assertEqual(report.dat.vis_bsp_present, True)
        self.assertEqual(report.processor_logs[0].problem_brush_count, 17)
        text = compiler_strategy.format_full_world_skeleton_compiled_validation_report(report)
        self.assertIn("status: validated_in_game", text)
        self.assertIn("PhysicsBSP floor probe", text)
        self.assertIn("problem_brushes=17", text)

    def _write_versioned_file(self, path, version):
        with open(path, "wb") as f:
            f.write(struct.pack("<I", int(version)))
            f.write(b"fixture")

    def _acceptance_harness(self, status, mismatches, warning=False):
        comparisons = tuple(
            compiler_strategy.BlackBoxCompilerSystemComparison(
                system=system,
                status="mismatch",
                reference_detail="reference",
                generated_detail="generated",
            )
            for system in mismatches
        )
        log = compiler_strategy.BlackBoxProcessorLogSummary(
            path="BOOTCAMP_0.log",
            status="loaded",
            warnings=("** test warning",) if warning else (),
        )
        return compiler_strategy.BlackBoxCompilerHarnessReport(
            status=status,
            processor_path="Processor.exe",
            source_ed_path="BOOTCAMP.ED",
            reference_dat_path="reference.DAT",
            output_dat_path="generated.DAT",
            captured_output=True,
            output_rewritten=True,
            reference=compiler_strategy.DatOutputSemanticSummary(path="reference.DAT", status="loaded"),
            generated=compiler_strategy.DatOutputSemanticSummary(path="generated.DAT", status="loaded"),
            comparisons=comparisons,
            processor_logs=(log,),
        )

    def _minimal_lta(self):
        return r'''
( world
  ( header ( versioncode 2 ) ( infostring "semantic fixture" ) )
  ( polyhedronlist (
    ( polyhedron
      ( color 128 64 32 )
      ( pointlist
        ( 0 0 0 255 255 255 255 )
        ( 128 0 0 255 255 255 255 )
        ( 128 0 128 255 255 255 255 )
        ( 0 0 128 255 255 255 255 )
      )
      ( polylist (
        ( editpoly
          ( f 0 1 2 3 )
          ( n 0 1 0 )
          ( dist 0 )
          ( textureinfo
            ( 0 0 0 )
            ( 1 0 0 )
            ( 0 0 1 )
            ( name "TEXTURES\World\Floor.dtx" )
          )
        )
      ) )
    )
  ) )
  ( nodehierarchy
    ( worldnode
      ( type brush )
      ( brushindex 0 )
      ( label "SemanticBrush" )
    )
  )
)
'''


if __name__ == "__main__":
    unittest.main()
