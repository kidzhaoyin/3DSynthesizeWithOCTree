import torch
import torch.nn as nn
import numpy as np

from projection import *
from custom_layers import *
import functools
import util

from pytorch_prototyping.pytorch_prototyping import *

class DeepVoxels(nn.Module):
    def __init__(self,
                 img_sidelength,
                 lifting_img_dims,
                 frustrum_img_dims,
                 grid_dims,
                 num_grid_feats=64,
                 nf0=64,
                 use_occlusion_net=True):
        ''' Initializes the DeepVoxels model.

        :param img_sidelength: The sidelength of the input images (for instance 512)
        :param lifting_img_dims: The dimensions of the feature map to be lifted.
        :param frustrum_img_dims: The dimensions of the canonical view volume that DeepVoxels are resampled to.
        :param grid_dims: The dimensions of the deepvoxels grid.
        :param grid_dims: The number of featres in the outermost layer of U-Nets.
        :param use_occlusion_net: Whether to use the OcclusionNet or not.
        '''
        super().__init__()

        self.use_occlusion_net = use_occlusion_net
        self.grid_dims = grid_dims

        self.norm = nn.BatchNorm2d

        self.lifting_img_dims = lifting_img_dims
        self.frustrum_img_dims = frustrum_img_dims
        self.grid_dims = grid_dims

        # The frustrum depth is the number of voxels in the depth dimension of the canonical viewing volume.
        # It's calculated as the length of the diagonal of the DeepVoxels grid.
        self.frustrum_depth = 2 * grid_dims[-1]

        self.nf0 = nf0 # Number of features to use in the outermost layer of all U-Nets
        self.n_grid_feats = num_grid_feats  # Number of features in the DeepVoxels grid.
        self.occnet_nf = 4  # Number of features to use in the 3D unet of the occlusion subnetwork

        # Feature extractor is an asymmetric UNet: Straight downsampling to 64x64, then a UNet with skip connections
        self.feature_extractor = FeatureExtractor(nf0=self.nf0,
                                                  out_channels=self.n_grid_feats,
                                                  input_resolution=img_sidelength,
                                                  output_sidelength=self.frustrum_img_dims[0])

        # Rendering net is an asymmetric UNet: UNet with skip connections and then straight upsampling
        self.rendering_net = RenderingNet(nf0=self.nf0,
                                          in_channels=self.n_grid_feats,
                                          input_resolution=self.frustrum_img_dims[0],
                                          img_sidelength=img_sidelength)

        if self.use_occlusion_net:
            self.occlusion_net = OcclusionNet(nf0=self.n_grid_feats,
                                              occnet_nf=self.occnet_nf,
                                              frustrum_dims=[self.frustrum_img_dims[0], self.frustrum_img_dims[1],
                                                             self.frustrum_depth])
            print(self.occlusion_net)
        else:
            self.depth_collapse_net = nn.Sequential(
                Conv2dSame(self.n_grid_feats * self.frustrum_depth,
                           out_channels=self.nf0 * self.grid_dims[-1] // 2,
                           kernel_size=3,
                           bias=False),
                self.norm(self.nf0 * self.grid_dims[-1] // 2),
                nn.ReLU(True),
                Conv2dSame(self.nf0 * self.grid_dims[-1] // 2,
                           out_channels=self.nf0 * self.grid_dims[-1] // 8,
                           kernel_size=3,
                           bias=False),
                self.norm(self.nf0 * self.grid_dims[-1] // 8),
                nn.ReLU(True),
                Conv2dSame(self.nf0 * self.grid_dims[-1] // 8,
                           out_channels=self.nf0,
                           kernel_size=3,
                           bias=False),
                self.norm(self.nf0),
                nn.ReLU(True),
            )
            print(self.frustrum_collapse_net)

        # The deepvoxels grid is registered as a buffer - meaning, it is safed together with model parameters, but is
        # not trainable.
        # self.register_buffer("deepvoxels",
        #                      torch.zeros(
        #                          (1, self.n_grid_feats, self.grid_dims[0], self.grid_dims[1], self.grid_dims[2])))
        self.depth = int(np.round(np.log(self.grid_dims[0])/np.log(2)))
        self.voxel_size = self.grid_dims[0]**3
        self.voxel_shape = (1, self.n_grid_feats, self.grid_dims[0], self.grid_dims[0], self.grid_dims[0])
        self.octree_length = int(8*(8**self.depth-1)/7)
        self.register_buffer("octree",
                             torch.zeros(
                                 (1, self.n_grid_feats, self.octree_length)))

        self.integration_net_octree = IntegrationNetOctree(self.n_grid_feats,
                                              #use_dropout=True,
                                              #coord_conv=True,
                                              per_feature=False,
                                              grid_dim=grid_dims[-1])

        self.inpainting_net = Unet3d(in_channels=self.n_grid_feats + 3,
                                     out_channels=self.n_grid_feats,
                                     num_down=2,
                                     nf0=self.n_grid_feats,
                                     max_channels=4 * self.n_grid_feats)

        print(100 * "*")
        print("inpainting_net")
        util.print_network(self.inpainting_net)
        print(self.inpainting_net)
        print("rendering net")
        util.print_network(self.rendering_net)
        print(self.rendering_net)
        print("feature extraction net")
        util.print_network(self.feature_extractor)
        print(self.feature_extractor)
        print(100 * "*")

        # Coordconv volumes
        coord_conv_volume = np.mgrid[-self.grid_dims[0] // 2:self.grid_dims[0] // 2,
                                     -self.grid_dims[1] // 2:self.grid_dims[1] // 2,
                                     -self.grid_dims[2] // 2:self.grid_dims[2] // 2]

        coord_conv_volume = np.stack(coord_conv_volume, axis=0).astype(np.float32)
        coord_conv_volume = coord_conv_volume / self.grid_dims[0]
        self.coord_conv_volume = torch.Tensor(coord_conv_volume).float().cuda()[None, :, :, :, :]

    def octree2voxel(self, octree):
        for i in range(self.depth-1):
            start_idx = int(8*(8**i-1)/7)
            depth_length = 8**(i+1)
            end_idx = start_idx + depth_length
            for j in range(depth_length):
                for f in range(self.n_grid_feats):
                    idx = j + depth_length
                    if octree[0,f,idx] < 1e-5:
                        octree[0,f,idx] = 0
                        octree[0,f,end_idx+j*8:end_idx+j*8+8] = torch.zeros((1,1,8))
        return octree[:,:,-self.voxel_size:].reshape(self.voxel_shape)

    def voxel2octree(self, dv_new):
        octree_new = torch.cat((torch.zeros((1, self.n_grid_feats, self.octree_length-self.voxel_size)), dv_new.reshape((1,self.n_grid_feats, self.voxel_size))), dim=2)
        for i in range(self.depth-1):
            depth_idx = self.depth - 2 - i
            start_idx = int(8*(8**depth_idx-1)/7)
            depth_length = 8**(depth_idx+1)
            end_idx = start_idx + depth_length
            for j in range(depth_length):
                idx = j + depth_length
                octree_new[:,:,idx] = torch.mean(octree_new[:,:,end_idx+j*8:end_idx+j*8+8], dim=-1)
        return octree_new

    def forward(self,
                input_img,
                proj_frustrum_idcs_list,
                proj_grid_coords_list,
                lift_volume_idcs,
                lift_img_coords,
                writer):
        if input_img is not None:
            # Training mode: Extract features from input img, lift them, and update the deepvoxels volume.
            img_feats = self.feature_extractor(input_img)
            temp_feat_vol = interpolate_lifting(img_feats, lift_volume_idcs, lift_img_coords, self.grid_dims)

            # dv_new = self.integration_net(temp_feat_vol, self.deepvoxels.detach(), writer)
            # self.deepvoxels.data = dv_new
            dv_old = self.octree2voxel(self.octree.detach())
            dv_new = self.integration_net_octree(temp_feat_vol, dv_old, writer)
            self.octree.data = self.voxel2octree(dv_new)
        else:
            # Testing mode: Use the pre-trained deepvoxels volume.
            dv_new = self.deepvoxels

        inpainting_input = torch.cat([dv_new, self.coord_conv_volume], dim=1)
        dv_inpainted = self.inpainting_net(inpainting_input)

        novel_views, depth_maps = list(), list()

        for i, (proj_frustrum_idcs, proj_grid_coords) in enumerate(zip(proj_frustrum_idcs_list, proj_grid_coords_list)):
            can_view_vol = interpolate_trilinear(dv_inpainted,
                                                 proj_frustrum_idcs,
                                                 proj_grid_coords,
                                                 self.frustrum_img_dims,
                                                 self.frustrum_depth)
            if self.use_occlusion_net:
                visibility_weights, depth_map = self.occlusion_net(can_view_vol)
                depth_maps.append(depth_map)

                collapsed_frustrum = torch.mean(visibility_weights * can_view_vol, dim=2)
                novel_image_features = collapsed_frustrum.contiguous().view(
                    [1, -1, self.frustrum_img_dims[0], self.frustrum_img_dims[1]])
            else:
                frustrum_collapse_input = can_view_vol.view(1, -1, self.frustrum_img_dims[0], self.frustrum_img_dims[1])
                novel_image_features = self.depth_collapse_net(frustrum_collapse_input)
                depth_maps.append(torch.zeros((1, 1, 64, 64)))

            rendered_img = 0.5 * self.rendering_net(novel_image_features)
            novel_views.append(rendered_img)

        return novel_views, depth_maps
