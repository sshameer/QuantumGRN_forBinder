import logging
import numpy as np
import pandas as pd
from .qcircuit import quantum_circuit
from .utils import Progbar


__all__ = ["model"]


def _loss_function(p_out, p_obs, method="kl-divergence"):
    if method == "kl-divergence":
        num = p_out
        den = p_obs
        log = np.log(num / den)
        return np.sum(num * log)

    elif method == "difference":
        err2 = np.power(p_out - p_obs, 2)
        return np.sum(err2)

    else:
        raise ValueError("No method to compute the loss function")


def _compute_error(p_out, p_obs):
    err = np.power(p_out - p_obs, 2)
    return np.sum(err)


def _laplace_smooth(distribution, ncells):
    """re-write this function such that it is readable"""
    alpha = 1
    dim = len(distribution)
    distribution = ncells * distribution
    N = np.sum(distribution)

    distribution = distribution + alpha
    N = N + alpha * dim
    return distribution / N, N


class model(quantum_circuit):
    """
    Attributes
    ----------
    ncells : int
        The number of cell in the data set.
    p_obs : ndarray
        The probability distribution observed in the data set.
    epochs : int
        The number of iterations for training if threshold
        is not reached.
    lr : int
        Learning rate for the gradient descent algorithm.
    method : str
        The methods for the loss function.
    train_encoder : bool
        Whether the parameters in the `L_enc` are trained.
    loss_threshold : float
        The threshold for the loss function value to stop
        the training.
    save_theta : bool
        Whether the theta values are saved for analysis during
        the training.
    loss : np.array
        Array to save the loss function values accross training.
    error : np.array
        Array to save the error values across training.
    gradient : pd.Dataframe
        Dataframe for saving the gradient of the loss function with
        respect of the parameters in the quantum circuit.
    Methods
    -------
    create_gradient()
        Create the pd.DataFrame object to save the gradient values.
    compute_gradient()
        Computes the gradient values for the loss function with
        respect of the parameters.
    train()
        Runs the gradient descent algorithm for theta optimization
        in the quantum circuit.
    export_training_theta(filename, sample=10)
        If save_theta is True, exports sampled values of theta across
        the training in to a file.
        Parameters
        ----------
            filenames : str
                The name of the file.
            sample : int
                The sample rate.
    """

    def __init__(self, ncells,
                 genes, theta, edges, p_obs, drop_zero=True,
                 epochs=1000, learning_rate=1, method="kl-divergence",
                 train_encoder=False, loss_threshold=None,
                 save_theta=False):
        """
        Parameters
        ----------
        ncells : int
            The number of cells in the data set.
        genes : list
            Gene list for Quantum GRN modelling.
        theta : pd.Series
            Theta values given edges in the QuantumGRN.
        edges : list
            Edges for the QuantumGRN.
        p_obs : ndarray
            Probability distribution `p^obs` observed in the data set.
        epochs : int
            The number of iterations for the gradient descent
            to compute. Default : 1000
        learning_rate : float
            The learning rate for the gradient descent to compute.
            Default : 1
        method : str
            Method for the loss function. Default : kl-divergence.
        train_encoder : bool
            Whether the parameters in the `L_enc` are trained or not.
            Default : False
        loss_threshold : float
            Threshold to stop the training of the parameters.
            Default : 1000
        save_theta : bool
            Whether the theta values are saved or not.
            Default : False
        """
        super().__init__(genes, theta, edges, drop_zero)
        logging.info("The QuantumGRN model is been initialized with "
                     "{ngenes} genes and {ncells} cells"
                     .format(ngenes=len(genes), ncells=ncells))
        self.ncells = ncells
        self.p_obs = p_obs.reshape(2**self.ngenes, 1)
        self.epochs = epochs
        self.lr = learning_rate,
        self.method= method
        self.train_encoder = train_encoder
        self.save_theta = save_theta
        self.loss = np.zeros(shape=(self.epochs,))
        self.error = np.zeros(shape=(self.epochs,))
        self.loss_threshold = 1e-6 * (2**self.ngenes) \
            if loss_threshold is None else loss_threshold

        self.gradient = None
        self.generate_circuit()
        self.create_gradient()


    def __str__(self):
        return ("QuantumGRN for {ngenes} genes with a sample of "
            "{ncells}").format(ngenes=self.ngenes,ncells=self.ncells)


    def _gradient_is_not_empty(self):
        """
        Validates whether the gradient dataframe is initialized or not.
        Raises
        ------
        AttributeError
            If gradient is not a None object.
        """
        if self.gradient is not None:
            logging.error("Gradients for the QuantumGRN optimization "
                          "are aleardy initialized")
            raise AttributeError("The quantum circuit for GRN model "
                                 "has gradients initialized")


    def create_gradient(self):
        """
        Creates the gradient attribute where the gradientes of the
        loss function with respect to the parameters values on each
        `L_enc` and `L_k` layers.
        """
        self._circuit_is_empty()
        self._gradient_is_not_empty()
        index = pd.MultiIndex.from_product([self.genes, self.genes],
                                           names=["control", "target"])
        self.gradient = pd.Series(np.zeros(shape=(self.ngenes**2,)), \
                                  index=index)


    def compute_gradient(self):
        """
        Computes the gradients of the loss function with respect to
        the parameters values on each quantum gates within `L_enc`
        and `L_k` layers and save it in the gradients attribute.

        It follows the procedure that is described in the publication,
        also updates the probability distributions `p^obs`, `p_h^out`
        and `p_h^obs`.
        Raises
        ------
        AttributeError
            The method attribute must be described in the manuscript.
        """
        self._der_is_empty()
        p_out = self.output_probabilities(self.drop_zero)
        h_p_out, h_N_out = _laplace_smooth(p_out, self.ncells)
        h_p_obs, h_N_obs = _laplace_smooth(self.p_obs, self.ncells)
        N_out = self.ncells
        v = self.output_state()
        dv = self.derivatives

        self.p_out = p_out
        self.h_p_out = h_p_out
        self.h_p_obs = h_p_obs

        if self.method == "kl-divergence":
            num = h_p_out
            den = h_p_obs
            log = np.log(num / den)

            if self.train_encoder:
                for gene in self.genes:
                    der_state = dv.loc[(gene, gene)].to_numpy()\
                        .reshape(2**self.ngenes, 1)
                    der_loss = (1 + log) * 2 * v * der_state \
                        * (N_out / h_N_out)
                    self.gradient[(gene, gene)] = np.sum(der_loss)

            for idx, edge in enumerate(self.edges):
                index = self.indexes[idx]
                der_state = dv.loc[edge].to_numpy().\
                    reshape(2**self.ngenes, 1)
                der_loss = (1 + log) * 2 * v * der_state \
                    * (N_out / h_N_out)
                self.gradient[edge] = np.sum(der_loss)

        else:
            raise AttributeError("The {method} method is not "
                                 "supported in the QuantumGRN "
                                 "modelling".format(method=self.method))


    def train(self):
        """
        Performs the optimization of the parameters in the quantum
        circuit model for GRN using a gradient descend-based algorithm.
        It also keeps updating the loss and error
        through the training in the loss and error arrays.

        If save_theta is True, the theta values are save into
        `training_theta`.

        It uses a progBar to display the progress of the optimization
        with respect of the total number of iterations. Some code
        is reused from Tensorflow (tf.kera.utils.Progbar)
        """
        logging.info("Starting the optimization for the QuantumGRN")
        progbar = Progbar(self.epochs)
        training_theta = []
        for epoch in range(self.epochs):
            terminate = True if epoch+1 == self.epochs else False
            self.compute_derivatives()
            self.compute_gradient()
            loss = _loss_function(self.h_p_out, self.h_p_obs,
                                 self.method)
            error = _compute_error(self.p_out, self.p_obs)
            self.loss[epoch] = loss
            self.error[epoch] = error
            progbar.update(epoch+1)

            if self.save_theta:
                training_theta.append(self.theta)

            if self.loss_threshold > loss or terminate:
                self.loss = self.loss[:epoch+1]
                self.error = self.error[:epoch+1]

                if self.save_theta:
                    self.training_theta = np.array(training_theta)

                end_msg = "Due to threshold reached" \
                    if self.loss_threshold > loss else \
                    "Due to the number of epochs reached"

                logging.info("Optimization completed!!.. {msg}"
                             .format(msg=end_msg))
                break

            self.theta = self.theta - self.lr * self.gradient
            self.generate_circuit()

    def export_training_theta(self, filename, sample=10):
        """
        Exports the theta values across the training if the attribute
        save_theta is True.
        Parameters
        ----------
        filename : str
            The file name to save the sampled theta values labeled
            by the gene pair (control-target).
            ie. 'theta_evolution.csv'
        sample : int
            The sampling period for theta values to avoid having
            large files.
            Default : 10
        Raises
        ------
        AttributeError
            If save_theta is False
        """
        if self.save_theta:
            logging.info("Theta values during optimization are "
                         "exported to {file}".format(file=filename))
            header=None
            for edge in self.theta.index:
                header = header + "," if header is not None else ""
                header += edge[0] + "-" + edge[1]

            np.savetxt(filename, self.training_theta[::sample],
                       delimiter=",", header=header)
        else:
            logging.error("Theta values were not saved during "
                          "training since the attribute save_theta "
                          "is {flag}".format(flag=self.save_theta))
            raise AttributeError("The attribute save_theta is "
                                 "{flag}".format(flag=self.save_theta))

