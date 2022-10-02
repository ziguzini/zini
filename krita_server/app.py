import logging
import os
import time

from fastapi import FastAPI
from PIL import Image
from webui import modules, shared

from .structs import Img2ImgRequest, Txt2ImgRequest, UpscaleRequest
from .utils import (
    collect_prompt,
    fix_aspect_ratio,
    get_sampler_index,
    get_upscaler_index,
    load_config,
    save_img,
    set_face_restorer,
)

app = FastAPI()

log = logging.getLogger(__name__)


@app.get("/config")
async def read_item():
    """Get information about backend API.

    Returns config from `krita_config.yaml`, the list of available upscalers,
    the path to the rendered image and image mask.

    Returns:
        Dict: information.
    """
    # TODO:
    # - function and route name isn't descriptive, feels more like get_state()
    # - response isn't well typed but is deeply tied into the krita_plugin side..
    # - ensuring the folders for images exist should be refactored out
    opt = load_config()["plugin"]
    sample_path = opt["sample_path"]
    os.makedirs(sample_path, exist_ok=True)
    filename = f"{int(time.time())}"
    path = os.path.join(sample_path, filename)
    src_path = os.path.abspath(path)
    return {
        "new_img": src_path + ".png",
        "new_img_mask": src_path + "_mask.png",
        "upscalers": [upscaler.name for upscaler in shared.sd_upscalers],
        **opt,
    }


@app.post("/txt2img")
async def f_txt2img(req: Txt2ImgRequest):
    """Post request for Txt2Img.

    Args:
        req (Txt2ImgRequest): Request.

    Returns:
        Dict: Outputs and info.
    """
    log.info(f"txt2img: {req}")

    opt = load_config()["txt2img"]
    set_face_restorer(
        req.face_restorer or opt["face_restorer"],
        req.codeformer_weight or opt["codeformer_weight"],
    )

    sampler_index = get_sampler_index(req.sampler_name or opt["sampler_name"])

    seed = opt["seed"] if req.seed is None else req.seed

    width, height = fix_aspect_ratio(
        req.base_size or opt["base_size"],
        req.max_size or opt["max_size"],
        req.orig_width,
        req.orig_height,
    )

    output_images, info, html = modules.txt2img.txt2img(
        req.prompt or collect_prompt(opt, "prompts"),
        req.negative_prompt or collect_prompt(opt, "negative_prompt"),
        "None",
        "None",
        req.steps or opt["steps"],
        sampler_index,
        req.use_gfpgan or opt["use_gfpgan"],
        req.tiling or opt["tiling"],
        req.batch_count or opt["n_iter"],
        req.batch_size or opt["batch_size"],
        req.cfg_scale or opt["cfg_scale"],
        seed,
        None,
        0,
        0,
        0,
        False,
        height,
        width,
        False,
        False,
        0,
        0,
    )

    sample_path = opt["sample_path"]
    os.makedirs(sample_path, exist_ok=True)
    resized_images = [
        modules.images.resize_image(0, image, req.orig_width, req.orig_height)
        for image in output_images
    ]
    outputs = [
        save_img(image, sample_path, filename=f"{int(time.time())}_{i}.png")
        for i, image in enumerate(resized_images)
    ]
    log.info(f"finished: {outputs}\n{info}")
    return {"outputs": outputs, "info": info}


@app.post("/img2img")
async def f_img2img(req: Img2ImgRequest):
    """Post request for Img2Img.

    Args:
        req (Img2ImgRequest): Request.

    Returns:
        Dict: Outputs and info.
    """
    log.info(f"img2img: {req}")

    opt = load_config()["img2img"]
    set_face_restorer(
        req.face_restorer or opt["face_restorer"],
        req.codeformer_weight or opt["codeformer_weight"],
    )

    sampler_index = get_sampler_index(req.sampler_name or opt["sampler_name"])

    seed = opt["seed"] if req.seed is None else req.seed

    mode = req.mode or opt["mode"]

    image = Image.open(req.src_path)
    orig_width, orig_height = image.size

    if mode == 1:
        mask = Image.open(req.mask_path).convert("L")
    else:
        mask = None

    # because API in webui changed
    if mode == 3:
        mode = 2

    upscaler_index = get_upscaler_index(req.upscaler_name or opt["upscaler_name"])

    base_size = req.base_size or opt["base_size"]
    if mode == 2:
        width, height = base_size, base_size
        if upscaler_index > 0:
            image = image.convert("RGB")
    else:
        width, height = fix_aspect_ratio(
            base_size, req.max_size or opt["max_size"], orig_width, orig_height
        )

    output_images, info, html = modules.img2img.img2img(
        0,
        req.prompt or collect_prompt(opt, "prompts"),
        req.negative_prompt or collect_prompt(opt, "negative_prompt"),
        "None",
        "None",
        image,
        {"image": image, "mask": mask},
        image,
        mask,
        mode,
        req.steps or opt["steps"],
        sampler_index,
        req.mask_blur or opt["mask_blur"],
        req.inpainting_fill or opt["inpainting_fill"],
        req.use_gfpgan or opt["use_gfpgan"],
        req.tiling or opt["tiling"],
        req.batch_count or opt["n_iter"],
        req.batch_size or opt["batch_size"],
        req.cfg_scale or opt["cfg_scale"],
        req.denoising_strength or opt["denoising_strength"],
        seed,
        None,
        0,
        0,
        0,
        False,
        height,
        width,
        opt["resize_mode"],
        req.inpaint_full_res or opt["inpaint_full_res"],
        32,
        False,  # req.invert_mask or opt['invert_mask'],
        "",
        "",
        # upscaler_index,
        # req.upscale_overlap or opt['upscale_overlap'],
        0,
    )

    resized_images = [
        modules.images.resize_image(0, image, orig_width, orig_height)
        for image in output_images
    ]

    if mode == 1:

        def remove_not_masked(img):
            masked_img = Image.new("RGBA", img.size, (0, 0, 0, 0))
            masked_img.paste(img, (0, 0), mask=mask)
            return masked_img

        resized_images = [remove_not_masked(x) for x in resized_images]

    sample_path = opt["sample_path"]
    os.makedirs(sample_path, exist_ok=True)
    outputs = [
        save_img(image, sample_path, filename=f"{int(time.time())}_{i}.png")
        for i, image in enumerate(resized_images)
    ]
    log.info(f"finished: {outputs}\n{info}")
    return {"outputs": outputs, "info": info}


@app.post("/upscale")
async def f_upscale(req: UpscaleRequest):
    """Post request for upscaling.

    Args:
        req (UpscaleRequest): Request.

    Returns:
        Dict: Output.
    """
    log.info(f"upscale: {req}")

    opt = load_config()["upscale"]
    image = Image.open(req.src_path).convert("RGB")
    orig_width, orig_height = image.size

    upscaler_index = get_upscaler_index(req.upscaler_name or opt["upscaler_name"])
    upscaler = shared.sd_upscalers[upscaler_index]

    if upscaler.name == "None":
        log.info(f"No upscaler selected, will do nothing")
        return

    if req.downscale_first or opt["downscale_first"]:
        image = modules.images.resize_image(0, image, orig_width // 2, orig_height // 2)

    upscaled_image = upscaler.upscale(image, 2 * orig_width, 2 * orig_height)
    resized_image = modules.images.resize_image(
        0, upscaled_image, orig_width, orig_height
    )

    sample_path = opt["sample_path"]
    os.makedirs(sample_path, exist_ok=True)
    output = save_img(resized_image, sample_path, filename=f"{int(time.time())}.png")
    log.info(f"finished: {output}")
    return {"output": output}
