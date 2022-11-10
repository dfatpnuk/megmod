from .ganblr import GANBLR
from sklearn.mixture import BayesianGaussianMixture
from sklearn.preprocessing import MinMaxScaler, LabelEncoder, OrdinalEncoder
from scipy.stats import truncnorm
from joblib import delayed, Parallel
from itertools import product
import numpy as np

class DMMDiscritizer:
    def __init__(self, random_state):
        self.__dmm_params = dict(weight_concentration_prior_type="dirichlet_process",
            n_components=2 * 5, reg_covar=0, init_params='random',
            max_iter=1500, mean_precision_prior=.1,
            random_state=random_state)

        self.__scaler = MinMaxScaler()
        #self.__ordinal_encoder = OrdinalEncoder(dtype=int)
        self.__dmms = []
        self.__arr_mu = []
        self.__arr_sigma = []

    def fit(self, x):
        """
        Do DMM Discritization.

        Parameter:
        ---------
        x (2d np.numpy): data to be discritize. must bu numeric data.

        Return:
        ----------
        self

        """
        assert(isinstance(x, np.ndarray))
        assert(len(x.shape) == 2)

        x_scaled = self.__scaler.fit_transform(x)
        self.__internal_fit(x_scaled)
        return self

    def transform(self, x) -> np.ndarray:        
        x = self.__scaler.transform(x)
        arr_modes = []
        for i, dmm in enumerate(self.__dmms):
            modes = dmm.predict(x[:,i:i+1])
            modes = LabelEncoder().fit_transform(modes)
            arr_modes.append(modes)
        return self.__internal_transform(x, arr_modes)

    def fit_transform(self, x) -> np.ndarray:
        assert(isinstance(x, np.ndarray))
        assert(len(x.shape) == 2)

        x_scaled = self.__scaler.fit_transform(x)
        arr_modes = self.__internal_fit(x_scaled)
        return self.__internal_transform(x_scaled, arr_modes)

    def __internal_fit(self, x):
        arr_mode = []
        for i in range(x.shape[1]):
            cur_column = x[:,i:i+1]
            dmm = BayesianGaussianMixture(**self.__dmm_params)
            y = dmm.fit_predict(cur_column)
            lbe = LabelEncoder().fit(y)
            mu  = dmm.means_[:len(lbe.classes_)]
            sigma = np.sqrt(dmm.covariances_[:len(lbe.classes_)])

            arr_mode.append(lbe.transform(y))
            #self.__arr_lbes.append(lbe)
            self.__dmms.append(dmm)
            self.__arr_mu.append(mu.ravel())
            self.__arr_sigma.append(sigma.ravel())
        return arr_mode

    def __internal_transform(self, x, arr_modes):
        _and = np.logical_and
        _not = np.logical_not

        discretized_data = []
        for i, (modes, mu, sigma) in enumerate(zip(
            arr_modes,
            self.__arr_mu, 
            self.__arr_sigma)):

            cur_column = x[:,i]
            cur_mu     = mu[modes]
            cur_sigma  = sigma[modes]
            x_std      = cur_column - cur_mu

            less_than_n3sigma = (x_std <= -3*cur_sigma)
            less_than_n2sigma = (x_std <= -2*cur_sigma)
            less_than_n1sigma = (x_std <=   -cur_sigma)
            less_than_0       = (x_std <=            0)
            less_than_1sigma  = (x_std <=    cur_sigma)
            less_than_2sigma  = (x_std <=  2*cur_sigma)
            less_than_3sigma  = (x_std <=  3*cur_sigma)
            
            discretized_x = 8 * modes
            discretized_x[_and(_not(less_than_n3sigma), less_than_n2sigma)] += 1
            discretized_x[_and(_not(less_than_n2sigma), less_than_n1sigma)] += 2
            discretized_x[_and(_not(less_than_n1sigma), less_than_0)]       += 3
            discretized_x[_and(_not(less_than_0)      , less_than_1sigma)]  += 4
            discretized_x[_and(_not(less_than_1sigma) , less_than_2sigma)]  += 5
            discretized_x[_and(_not(less_than_2sigma) , less_than_3sigma)]  += 6
            discretized_x[_not(less_than_3sigma)]                           += 7
            discretized_data.append(discretized_x.reshape(-1,1))
        
        #return self.__ordinal_encoder.fit_transform(np.hstack(discretized_data))
        return np.hstack(discretized_data)

    def inverse_transform(self, x, n_jobs=-1, verbose=1) -> np.ndarray:
        #x = self.__ordinal_encoder.inverse_transform(x)
        x_modes = x // 8
        x_bins = x % 8
            
        def __parallel_unit(i, mu, sigma):
            cur_column_modes = x_modes[:,i]
            cur_column_bins  = x_bins[:,i]
            cur_column_mode_uniques    = np.unique(cur_column_modes)
            inversed_x = np.zeros_like(cur_column_modes, dtype=float)      

            for mode in cur_column_mode_uniques:
                cur_mode_idx = cur_column_modes == mode
                cur_mode_mu = mu[mode]
                cur_mode_sigma = sigma[mode]

                sample_results = self.__sample_from_truncnorm(cur_column_bins[cur_mode_idx], cur_mode_mu, cur_mode_sigma)
                inversed_x[cur_mode_idx] = sample_results

            return inversed_x.reshape(-1,1)
        
        inversed_data = np.hstack(Parallel(n_jobs=n_jobs, verbose=verbose)(delayed(__parallel_unit)(i, mu, sigma)
            for i, (mu, sigma) in enumerate(self.__arr_mu, self.__arr_sigma)))

        #for i, (mu, sigma) in enumerate(zip(
        #    self.__arr_mu, 
        #    self.__arr_sigma)):
        #   
        #    cur_column_modes = x_modes[:,i]
        #    cur_column_bins  = x_bins[:,i]
        #    cur_column_mode_uniques    = np.unique(cur_column_modes)

        #    inversed_x = np.zeros_like(cur_column_modes, dtype=float)      

        #    for mode in cur_column_mode_uniques:
        #        cur_mode_idx = cur_column_modes == mode
        #        cur_mode_mu = mu[mode]
        #        cur_mode_sigma = sigma[mode]

        #        sample_results = self.__sample_from_truncnorm(cur_column_bins[cur_mode_idx], cur_mode_mu, cur_mode_sigma)
        #        inversed_x[cur_mode_idx] = sample_results
        #    
        #    inversed_data.append(inversed_x.reshape(-1, 1))

        return self.__scaler.inverse_transform(inversed_data)

    @staticmethod
    def __sample_from_truncnorm(bins, mu, sigma, random_states): 
        sampled_results = np.zeros_like(bins, dtype=float)
        def __sampling(idx, range_min, range_max):
            sampling_size = np.sum(idx)
            if sampling_size != 0:
                sampled_results[idx] = truncnorm.rvs(range_min, range_max, loc=mu, scale=sigma, size=sampling_size, random_states=random_states)
        
        #shape param (min, max) of scipy.stats.truncnorm.rvs are still defined with respect to the standard normal
        __sampling(bins == 0, np.NINF, -3)
        __sampling(bins == 1, -3, -2)
        __sampling(bins == 2, -2, -1)
        __sampling(bins == 3, -1,  0)
        __sampling(bins == 4, 0,   1)
        __sampling(bins == 5, 1,   2)
        __sampling(bins == 6, 2,   3)
        __sampling(bins == 7, 3, np.inf)
        return sampled_results     

class GANBLRPP:

    def __init__(self, numerical_columns, random_state=None):
        self.__discritizer = DMMDiscritizer(random_state)
        self.__ganblr = GANBLR()
        self._numerical_columns = numerical_columns
        pass
    
    def fit(self, x, y, k=0, batch_size=32, epochs=10, warmup_epochs=1, verbose=1):
        '''
        Fit the model to the given data.
S
        Parameters:
        --------
        x, y (numpy.ndarray): Dataset to fit the model. The data should be discrete.
        
        k (int, optional): Parameter k of ganblr model. Must be greater than 0. No more than 2 is Suggested.

        batch_size (int, optional): Size of the batch to feed the model at each step. Defaults to
            :attr:`32`.
        
        epochs (int, optional): Number of epochs to use during training. Defaults to :attr:`10`.
        
        warmup_epochs (int, optional): Number of epochs to use in warmup phase. Defaults to :attr:`1`.
        
        verbose (int, optional): Whether to output the log. Use 1 for log output and 0 for complete silence.
        '''
        numerical_columns = self._numerical_columns
        x[:,numerical_columns] = self.__discritizer.fit_transform(x[:,numerical_columns])
        return self.__ganblr.fit(x, y, k, batch_size, epochs, warmup_epochs, verbose)
    
    def sample(self, size=None, verbose=1):
        """
        Generate synthetic data.     

        Parameters:
        -----------------
        size (int, optional): Size of the data to be generated. Defaults to: attr:`None`.

        verbose (int, optional): Whether to output the log. Use 1 for log output and 0 for complete silence.
        
        n_jobs (int, optional):
        Return:
        -----------------
        numpy.ndarray: Generated synthetic data.

        """
        if verbose:
            print('Step 1 of 2: Sampling discrete data from GANBLR.')
        ordinal_data = self.__ganblr._sample(size, verbose=verbose)
        syn_x = self.__ganblr._ordinal_encoder.inverse_transform(ordinal_data[:,:-1])
        syn_y = self.__ganblr._label_encoder.inverse_transform(ordinal_data[:,-1]).reshape(-1,1)
        if verbose:
            print('step 2 of 2: Sampling numerical data.')
        numerical_columns = self._numerical_columns
        numerical_data = self.__discritizer.inverse_transform(syn_x[:,numerical_columns], n_jobs=1)
        syn_x[:,numerical_columns] = numerical_data
        return np.hstack([syn_x, syn_y])

    def evaluate(self, x, y, model='lr'):
        """
        Perform a TSTR(Training on Synthetic data, Testing on Real data) evaluation.

        Parameters:
        ------------------
         x, y (numpy.ndarray): test dataset.

         model: the model used for evaluate. Should be one of ['lr', 'mlp', 'rf'], or a model class that have sklearn-style 'fit' method.

        Return:
        --------
        accuracy score (float).
        """
        numerical_columns = self._numerical_columns
        x[:,numerical_columns] = self.__discritizer.transform(x[:,numerical_columns])
        return self.__ganblr.evaluate(x, y, model)
