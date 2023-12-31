from pathlib import Path
import pickle
import pandas as pd
import torch
import torchaudio
from tqdm.auto import tqdm
from data_utils import motif2idx, sample_instance_intervals, generate_non_overlapping_intervals, sample_non_overlapping_interval

class LeitmotifDataset:
    def __init__(self, pkl_path:Path, instances_path:Path, duration_sec=15, duration_samples=646, audio_path=None):
        self.cqt = {}
        self.instances_gt = {}
        self.samples = []
        self.none_samples = []

        self.duration_sec = duration_sec
        if audio_path is None:
            self.audio_path = Path("data/WagnerRing_Public/01_RawData/audio_wav")
        else:
            self.audio_path = audio_path
        
        print("Creating dataset...")
        pkl_fns = sorted(list(pkl_path.glob("*.pkl")))
        for fn in tqdm(pkl_fns):
            # Load CQT data
            with open(fn, "rb") as f:
                self.cqt[fn.stem] = torch.tensor(pickle.load(f)).T # (time, n_bins)
            total_duration = self.cqt[fn.stem].shape[0] * 512 // 22050

            # Create ground truth instance tensors
            self.instances_gt[fn.stem] = torch.zeros((self.cqt[fn.stem].shape[0], 21))
            instances = list(pd.read_csv(instances_path / f"P-{fn.stem.split('_')[0]}/{fn.stem.split('_')[1]}.csv", sep=";").itertuples(index=False, name=None))
            for instance in instances:
                motif = instance[0]
                start = instance[1]
                end = instance[2]
                start_idx = int(round(start * 22050 / 512))
                end_idx = int(round(end * 22050 / 512))
                motif_idx = motif2idx[motif]
                self.instances_gt[fn.stem][start_idx:end_idx, motif_idx] = 1

            # Add "none" class to ground truth
            self.instances_gt[fn.stem][:, -1] = 1 - self.instances_gt[fn.stem][:, :-1].max(dim=1).values
            
            # Sample leitmotif instances
            version = fn.stem.split("_")[0]
            act = fn.stem.split("_")[1]
            samples_act = sample_instance_intervals(instances, duration_sec, total_duration)
            # (version, act, motif, start_sec, end_sec)
            samples_act = [(version, act, x[0], int(round(x[1] * 22050 / 512)), int(round(x[1] * 22050 / 512) + duration_samples)) for x in samples_act]
            self.samples.extend(samples_act)

            # Sample non-leitmotif instances
            occupied = instances.copy()
            none_intervals = generate_non_overlapping_intervals(instances, total_duration)
            none_samples_act = []
            depleted = False
            while not depleted:
                samp = sample_non_overlapping_interval(none_intervals, duration_sec)
                if samp is None:
                    depleted = True
                else:
                    occupied.append((None, samp[0], samp[1]))
                    none_intervals = generate_non_overlapping_intervals(occupied, total_duration)
                    none_samples_act.append(samp)
            none_samples_act.sort(key=lambda x: x[0])
            # (version, act, start_sec, end_sec)
            none_samples_act = [(version, act, int(round(x[0] * 22050 / 512)), int(round(x[0] * 22050 / 512) + duration_samples)) for x in none_samples_act]
            self.none_samples.extend(none_samples_act)

    def get_subset_idxs(self, versions=None, acts=None):
        if versions is None and acts is None:
            return list(range(len(self.samples) + len(self.none_samples)))
        elif versions is None:
            samples = [idx for (idx, x) in enumerate(self.samples) if x[1] in acts]
            none_samples = [idx + len(self.samples) for (idx, x) in enumerate(self.none_samples) if x[1] in acts]
            return samples + none_samples
        elif acts is None:
            samples = [idx for (idx, x) in enumerate(self.samples) if x[0] in versions]
            none_samples = [idx + len(self.samples) for (idx, x) in enumerate(self.none_samples) if x[0] in versions]
            return samples + none_samples
        else:
            samples = [idx for (idx, x) in enumerate(self.samples) if x[0] in versions and x[1] in acts]
            none_samples = [idx + len(self.samples) for (idx, x) in enumerate(self.none_samples) if x[0] in versions and x[1] in acts]
            return samples + none_samples

    def query_motif(self, motif:str):
        """
        Query with motif name. (e.g. "Nibelungen")\n
        Returns list of (idx, version, act, start_sec, end_sec)
        """
        motif_samples = [(idx, x[0], x[1], x[3] * 512 // 22050, x[4] * 512 // 22050) for (idx, x) in enumerate(self.samples) if x[2] == motif]
        if len(motif_samples) > 0:
            return motif_samples
        else:
            return None

    def preview_idx(self, idx):
        """
        Returns (version, act, motif, y, sr, start_sec, instances_gt)
        """
        if idx < len(self.samples):
            version, act, motif, start, end = self.samples[idx]
            fn = f"{version}_{act}"
            gt = self.instances_gt[fn][start:end, :]
            start_sec = start * 512 // 22050
            y, sr = torchaudio.load(self.audio_path / f"{fn}.wav")
            start = start_sec * sr
            end = start + (self.duration_sec * sr)
            y = y[:, start:end]
            return version, act, motif, y, sr, start_sec, gt
        else:
            idx -= len(self.samples)
            version, act, start, end = self.none_samples[idx]
            fn = f"{version}_{act}"
            gt = torch.zeros((end - start, 21))
            start_sec = start * 512 // 22050
            y, sr = torchaudio.load(self.audio_path / f"{fn}.wav")
            start = start_sec * sr
            end = start + (self.duration_sec * sr)
            y = y[:, start:end]
            return version, act, "none", y, sr, start_sec, gt

    def __len__(self):
        return len(self.samples) + len(self.none_samples)
    
    def __getitem__(self, idx):
        if idx < len(self.samples):
            version, act, _, start, end = self.samples[idx]
            fn = f"{version}_{act}"
            return self.cqt[fn][start:end, :], self.instances_gt[fn][start:end, :]
        else:
            idx -= len(self.samples)
            version, act, start, end = self.none_samples[idx]
            fn = f"{version}_{act}"
            return self.cqt[fn][start:end, :], torch.zeros((end - start, 21))
        
class Subset:
    def __init__(self, dataset, indices):
        self.dataset = dataset
        self.indices = indices

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, idx):
        return self.dataset[self.indices[idx]]
    
def collate_fn(batch):
    cqt, gt = zip(*batch)
    cqt = torch.stack(cqt)
    gt = torch.stack(gt)
    return cqt, gt