import numpy as np
import math
import os
import cv2
import progressbar
from skimage.measure import compare_mse as mse
from skimage.measure import compare_psnr as psnr
from skimage.measure import compare_ssim as ssim

def to_gray(image):
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

with open('evaluate.txt', 'r') as f:
    dir_true = f.readline().strip()
    dir_test = []
    for l in f:
        dir_test.append(l.strip())

for d in dir_test:
    images = os.listdir(dir_true)
    mse_total = 0
    psnr_total = 0
    ssim_total = 0

    widgets=[
        ' [', progressbar.Timer(), '] ',
        progressbar.Bar(),
        ' (', progressbar.ETA(), ') ',
        ]

    bar = progressbar.ProgressBar(max_value=len(images), widgets=widgets)
    counter = 0

    for i in images:
        original = cv2.imread(os.path.join(dir_true, i), 1)
        contrast = cv2.imread(os.path.join(d, "img_" + i), 1)
        w = int((original.shape[0] - contrast.shape[0]) / 2)
        h = int((original.shape[1] - contrast.shape[1]) / 2)
        crop_img = original[w:w+contrast.shape[0], h:h+contrast.shape[1]]
        mse_total += mse(crop_img,contrast)
        psnr_total += psnr(crop_img,contrast)
        ssim_total += ssim(to_gray(crop_img),to_gray(contrast))

        counter += 1
        bar.update(counter)

    print("Average MSE %.4f" % (mse_total / len(images)))
    print("Average PSNR %.4f" % (psnr_total / len(images)))
    print("Average SSIM %.4f" % (ssim_total / len(images)))


