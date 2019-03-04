from os import makedirs
from os.path import join
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from networks.networks import ImageEncoder, VoxelDecoder
from .marrnetbase import MarrnetBaseModel


class Model(MarrnetBaseModel):
    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument(
            '--canon_sup',
            action='store_true',
            help="Use canonical-pose voxels as supervision"
        )
        return parser, set()

    def __init__(self, opt, logger):
        super(Model, self).__init__(opt, logger)
        if opt.canon_sup:
            voxel_key = 'voxel_canon'
        else:
            voxel_key = 'voxel'
        self.voxel_key = voxel_key
        self.requires = ['rgb', 'depth', 'normal', 'silhou', voxel_key]
        self.net = Net(4)
        self.criterion = nn.BCEWithLogitsLoss(reduction='elementwise_mean')
        self.optimizer = self.adam(
            self.net.parameters(),
            lr=opt.lr,
            **self.optim_params
        )
        #self.optimizer = optim.SGD(
        #    self.net.parameters(),
        #    lr=opt.lr,
        #    momentum=0.9
        #)
        print(self.optimizer)
        self._nets = [self.net]
        self._optimizers.append(self.optimizer)
        self.input_names = ['depth1', 'normal1', 'silhou1', 'depth2', 'normal2', 'silhou2']
        self.gt_names = [voxel_key]
        self.init_vars(add_path=True)
        self._metrics = ['loss']
        self.init_weight(self.net)

    def __str__(self):
        return "MarrNet-2 predicting voxels from 2.5D sketches"

    def _train_on_batch(self, epoch, batch_idx, batch):
        self.net.zero_grad()
        pred = self.predict(batch)
        
#        print(pred)
#        print('-------------pred above-------------')
#        sigm = nn.Sigmoid()
#        print(sigm(pred))
#        print('-------------sig(pred) above-------------')
#        print(getattr(self._gt, self.voxel_key))
#        print('-------------sig(pred) above-------------')
#        print(sigm(pred)>0.5)
#        print('----------------ground true----------')
        loss, loss_data = self.compute_loss(pred)
        loss.backward()
        self.optimizer.step()
        batch_size = len(batch['rgb1_path'])
        batch_log = {'size': batch_size, **loss_data}
        return batch_log

    def _vali_on_batch(self, epoch, batch_idx, batch):
        pred = self.predict(batch, no_grad=True)
        _, loss_data = self.compute_loss(pred)
        if np.mod(epoch, self.opt.vis_every_vali) == 0:
            if batch_idx < self.opt.vis_batches_vali:
                outdir = join(self.full_logdir, 'epoch%04d_vali' % epoch)
                makedirs(outdir, exist_ok=True)
                output = self.pack_output(pred, batch)
                self.visualizer.visualize(output, batch_idx, outdir)
                np.savez(join(outdir, 'batch%04d' % batch_idx), **output)
        batch_size = len(batch['rgb1_path'])
        batch_log = {'size': batch_size, **loss_data}
        return batch_log

    def pack_output(self, pred, batch, add_gt=True):
        out = {}
        out['rgb1_path'] = batch['rgb1_path']
        out['rgb2_path'] = batch['rgb2_path']
        out['pred_voxel'] = pred.detach().cpu().numpy()
        if add_gt:
            out['gt_voxel'] = batch[self.voxel_key].numpy()
            out['normal1_path'] = batch['normal1_path']
            out['normal2_path'] = batch['normal2_path']
            out['depth1_path'] = batch['depth1_path']
            out['depth2_path'] = batch['depth2_path']
            out['silhou1_path'] = batch['silhou1_path']
            out['silhou2_path'] = batch['silhou2_path']
        return out

    def compute_loss(self, pred):
        loss = self.criterion(pred, getattr(self._gt, self.voxel_key))
        loss_data = {}
        loss_data['loss'] = loss.mean().item()
        return loss, loss_data


class Net(nn.Module):
    """
    2.5D maps to 3D voxel
    """

    def __init__(self, in_planes, encode_dims=200, silhou_thres=0):
        super().__init__()
        self.encoder = ImageEncoder(in_planes, encode_dims=encode_dims)
        self.decoder = VoxelDecoder(n_dims=encode_dims*2, nf=512)
        self.silhou_thres = silhou_thres

    def forward(self, input_struct):
        #depth = input_struct.depth
        #normal = input_struct.normal
        #silhou = input_struct.silhou
        
        ##########################################
        
        depth1 = input_struct.depth1
        normal1 = input_struct.normal1
        silhou1 = input_struct.silhou1
        depth2 = input_struct.depth2
        normal2 = input_struct.normal2
        silhou2 = input_struct.silhou2
        # Mask
        is_bg1 = silhou1 < self.silhou_thres
        depth1[is_bg1] = 0
        normal1[is_bg1.repeat(1, 3, 1, 1)] = 0
        
        is_bg2 = silhou2 < self.silhou_thres
        depth2[is_bg2] = 0
        normal2[is_bg2.repeat(1, 3, 1, 1)] = 0
        x1 = torch.cat((depth1, normal1), 1)
        x2 = torch.cat((depth2, normal2), 1)
        # Forward
        latent_vec1 = self.encoder(x1)
        latent_vec2 = self.encoder(x2)
        latent_vec = torch.cat((latent_vec1, latent_vec2), 1)
        vox = self.decoder(latent_vec)
        return vox
