import unittest
from unittest import mock

from runtime.image_runtime import (
    ADAPTERS,
    PONY_CHECKPOINT,
    QWEN_TEXT_ENCODER,
    QWEN_UNET,
    QWEN_VAE,
    FLUX2_KLEIN_FP8,
    FLUX2_TEXT_ENCODER,
    FLUX2_VAE,
    ImageRuntime,
    adapter_for,
    artifact_specs,
    build_prompt,
    checkpoint_spec,
)


class ImageAdapterTests(unittest.TestCase):
    def test_matches_pony_v6(self):
        matched = adapter_for(
            "Pony Diffusion V6 XL",
            "pony_diffusion_v6_xl",
            "image_anime/Snupihog__Pony_Diffusion_V6_XL",
        )
        self.assertIsNotNone(matched)
        self.assertEqual(matched[0], "pony_diffusion_v6_xl")

    def test_matches_qwen_image_linked_package(self):
        matched = adapter_for(
            "Qwen-Image-2.1 INT8",
            "qwen_image_2_1_int8",
            "image_base/Qwen-Image-2.1-GGUF",
        )
        self.assertIsNotNone(matched)
        self.assertEqual(matched[0], "qwen_image_2_1_int8")

    def test_qwen_requires_all_three_drive_artifacts(self):
        adapter = ADAPTERS["qwen_image_2_1_int8"]
        specs = artifact_specs(
            {
                "files": [
                    {
                        "id": "testQwenUnet123456789",
                        "name": QWEN_UNET,
                        "size": 4_604_557_984,
                    },
                    {
                        "id": "testQwenClip123456789",
                        "name": QWEN_TEXT_ENCODER,
                        "size": 9_350_798_360,
                    },
                    {
                        "id": "testQwenVae1234567890",
                        "name": QWEN_VAE,
                        "size": 675_509_688,
                    },
                ]
            },
            adapter,
        )
        self.assertEqual(set(specs), {"unet", "clip", "vae"})
        self.assertEqual(specs["unet"].name, QWEN_UNET)

    def test_qwen_rejects_missing_text_encoder(self):
        with self.assertRaises(FileNotFoundError):
            artifact_specs(
                {
                    "files": [
                        {
                            "id": "testQwenUnet123456789",
                            "name": QWEN_UNET,
                            "size": 4_604_557_984,
                        },
                        {
                            "id": "testQwenVae1234567890",
                            "name": QWEN_VAE,
                            "size": 675_509_688,
                        },
                    ]
                },
                ADAPTERS["qwen_image_2_1_int8"],
            )

    def test_builds_qwen_image_gguf_prompt(self):
        prompt = build_prompt(
            {
                "unet": QWEN_UNET,
                "clip": QWEN_TEXT_ENCODER,
                "vae": QWEN_VAE,
            },
            {
                "prompt": "cinematic mountain lake",
                "negative_prompt": "blurry",
                "width": 768,
                "height": 768,
                "steps": 20,
                "cfg": 1.0,
                "seed": 42,
            },
            ADAPTERS["qwen_image_2_1_int8"],
            "qwenjob",
        )
        self.assertEqual(prompt["1"]["class_type"], "UnetLoaderGGUF")
        self.assertEqual(prompt["1"]["inputs"]["unet_name"], QWEN_UNET)
        self.assertEqual(prompt["2"]["inputs"]["type"], "qwen_image")
        self.assertEqual(prompt["4"]["class_type"], "TextEncodeQwenImage21")
        self.assertEqual(prompt["4"]["inputs"]["resolution"], 1024)
        self.assertEqual(prompt["6"]["inputs"]["sampler_name"], "euler")
        self.assertEqual(prompt["6"]["inputs"]["scheduler"], "simple")
        self.assertEqual(prompt["8"]["class_type"], "SaveImage")

    def test_matches_flux2_klein_fp8(self):
        matched = adapter_for(
            "FLUX.2 Klein 4B FP8",
            "flux2_klein_4b_fp8",
            "image_base/black-forest-labs__FLUX.2-klein-4b-fp8",
        )
        self.assertIsNotNone(matched)
        self.assertEqual(matched[0], "flux2_klein_4b_fp8")

    def test_flux2_requires_three_artifacts(self):
        specs = artifact_specs(
            {
                "files": [
                    {
                        "id": "testFlux2Unet123456789",
                        "name": FLUX2_KLEIN_FP8,
                        "size": 4_070_624_520,
                    },
                    {
                        "id": "testFlux2Clip123456789",
                        "name": FLUX2_TEXT_ENCODER,
                        "size": 8_044_982_048,
                    },
                    {
                        "id": "testFlux2Vae1234567890",
                        "name": FLUX2_VAE,
                        "size": 336_213_556,
                    },
                ]
            },
            ADAPTERS["flux2_klein_4b_fp8"],
        )
        self.assertEqual(set(specs), {"unet", "clip", "vae"})
        self.assertEqual(specs["unet"].name, FLUX2_KLEIN_FP8)

    def test_flux2_rejects_wrong_exact_size(self):
        with self.assertRaises(ValueError):
            artifact_specs(
                {
                    "files": [
                        {
                            "id": "testFlux2UnetWrongSize",
                            "name": FLUX2_KLEIN_FP8,
                            "size": 4_070_624_519,
                        },
                        {
                            "id": "testFlux2Clip123456789",
                            "name": FLUX2_TEXT_ENCODER,
                            "size": 8_044_982_048,
                        },
                        {
                            "id": "testFlux2Vae1234567890",
                            "name": FLUX2_VAE,
                            "size": 336_213_556,
                        },
                    ]
                },
                ADAPTERS["flux2_klein_4b_fp8"],
            )

    def test_flux2_rejects_missing_companion(self):
        with self.assertRaises(FileNotFoundError):
            artifact_specs(
                {
                    "files": [
                        {
                            "id": "testFlux2Unet123456789",
                            "name": FLUX2_KLEIN_FP8,
                            "size": 4_070_624_520,
                        }
                    ]
                },
                ADAPTERS["flux2_klein_4b_fp8"],
            )

    def test_builds_flux2_distilled_prompt(self):
        prompt = build_prompt(
            {
                "unet": FLUX2_KLEIN_FP8,
                "clip": FLUX2_TEXT_ENCODER,
                "vae": FLUX2_VAE,
            },
            {
                "prompt": "studio photo of a glass sculpture",
                "width": 1024,
                "height": 1024,
                "steps": 4,
                "cfg": 1.0,
                "seed": 99,
            },
            ADAPTERS["flux2_klein_4b_fp8"],
            "fluxjob",
        )
        self.assertEqual(prompt["1"]["class_type"], "UNETLoader")
        self.assertEqual(prompt["1"]["inputs"]["unet_name"], FLUX2_KLEIN_FP8)
        self.assertEqual(prompt["2"]["inputs"]["type"], "flux2")
        self.assertEqual(prompt["5"]["class_type"], "ConditioningZeroOut")
        self.assertEqual(prompt["8"]["class_type"], "Flux2Scheduler")
        self.assertEqual(prompt["8"]["inputs"]["steps"], 4)
        self.assertEqual(prompt["9"]["class_type"], "CFGGuider")
        self.assertEqual(prompt["9"]["inputs"]["cfg"], 1.0)
        self.assertEqual(prompt["10"]["class_type"], "EmptyFlux2LatentImage")
        self.assertEqual(prompt["11"]["class_type"], "SamplerCustomAdvanced")
        self.assertEqual(prompt["13"]["class_type"], "SaveImage")

    def test_selects_complete_drive_checkpoint(self):
        spec = checkpoint_spec(
            {
                "files": [
                    {
                        "id": "testPonyCheckpoint1234567890",
                        "name": PONY_CHECKPOINT,
                        "size": 6_938_041_050,
                    }
                ]
            },
            ADAPTERS["pony_diffusion_v6_xl"],
        )
        self.assertEqual(spec.name, PONY_CHECKPOINT)
        self.assertEqual(spec.size, 6_938_041_050)

    def test_rejects_incomplete_checkpoint(self):
        with self.assertRaises(ValueError):
            checkpoint_spec(
                {
                    "files": [
                        {
                            "id": "testPonyCheckpoint1234567890",
                            "name": PONY_CHECKPOINT,
                            "size": 1024,
                        }
                    ]
                },
                ADAPTERS["pony_diffusion_v6_xl"],
            )

    def test_rejects_different_large_safetensors(self):
        with self.assertRaises(FileNotFoundError):
            checkpoint_spec(
                {
                    "files": [
                        {
                            "id": "testOtherCheckpoint123456789",
                            "name": "unrelated-large-model.safetensors",
                            "size": 8_000_000_000,
                        }
                    ]
                },
                ADAPTERS["pony_diffusion_v6_xl"],
            )

    def test_required_node_value_validation_accepts_flux2(self):
        runtime = object.__new__(ImageRuntime)
        adapter = {
            "label": "FLUX.2",
            "required_nodes": ("CLIPLoader",),
            "required_node_values": {
                "CLIPLoader": {"type": "flux2"},
            },
        }
        object_info = {
            "CLIPLoader": {
                "input": {
                    "required": {
                        "type": [["stable_diffusion", "qwen_image", "flux2"]]
                    }
                }
            }
        }
        with mock.patch(
            "runtime.image_runtime.json_request",
            return_value=object_info,
        ):
            runtime._verify_required_nodes(adapter)

    def test_required_node_value_validation_rejects_old_clip_loader(self):
        runtime = object.__new__(ImageRuntime)
        adapter = {
            "label": "FLUX.2",
            "required_nodes": ("CLIPLoader",),
            "required_node_values": {
                "CLIPLoader": {"type": "flux2"},
            },
        }
        object_info = {
            "CLIPLoader": {
                "input": {
                    "required": {
                        "type": [["stable_diffusion", "qwen_image"]]
                    }
                }
            }
        }
        with mock.patch(
            "runtime.image_runtime.json_request",
            return_value=object_info,
        ):
            with self.assertRaises(RuntimeError):
                runtime._verify_required_nodes(adapter)

    def test_builds_sdxl_comfy_prompt(self):
        prompt = build_prompt(
            PONY_CHECKPOINT,
            {
                "prompt": "score_9, source_anime, adult traveler",
                "negative_prompt": "blurry, watermark",
                "width": 1024,
                "height": 1024,
                "steps": 28,
                "cfg": 5,
                "seed": 1234,
            },
            ADAPTERS["pony_diffusion_v6_xl"],
            "job123",
        )
        self.assertEqual(prompt["1"]["class_type"], "CheckpointLoaderSimple")
        self.assertEqual(prompt["1"]["inputs"]["ckpt_name"], PONY_CHECKPOINT)
        self.assertEqual(prompt["2"]["inputs"]["stop_at_clip_layer"], -2)
        self.assertEqual(prompt["6"]["inputs"]["steps"], 28)
        self.assertEqual(prompt["6"]["inputs"]["cfg"], 5.0)
        self.assertEqual(prompt["6"]["inputs"]["seed"], 1234)
        self.assertEqual(prompt["8"]["class_type"], "SaveImage")


if __name__ == "__main__":
    unittest.main()
