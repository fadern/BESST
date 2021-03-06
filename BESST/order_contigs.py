'''
    Created on May 30, 2014

    @author: ksahlin

    This file is part of BESST.

    BESST is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    BESST is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with BESST.  If not, see <http://www.gnu.org/licenses/>.
    '''

import copy
import math
import random
from collections import Counter

from pulp import *
from mathstats.normaldist.normal import normpdf
from mathstats.normaldist.truncatedskewed import param_est as GC




class Contig(object):
    """Container with contig information for a contig in a path"""
    def __init__(self, index, length):
        super(Contig, self).__init__()
        self.index = index
        self.length = length
        self.position = None
        

class Path(object):
    """Contains all information of a path. This is basically a supgraph of the 
    Scaffold graph in BESST contianing all contigs in a path that has high score 
    and is going to be made into a scaffold. 

    Path is an object with methods for calculating likelihood of a path given link observations.
    Due to computational requiremants, we don't calculate the true likelihoos all paths usually have thousands
    of links. Instead, we take the average link obervation between all contigs. This will not give true ML 
    estimates but speeds up calculation with thousands of x order. In practice, using average link obervation 
    For ML estimation will give a fairly good prediction. For this cheat, see comment approx 15 lines below.  """
    def __init__(self, ctg_lengths, observations, param, initial_path=False):
        super(Path, self).__init__()
        self.mean = param.mean_ins_size
        self.stddev = param.std_dev_ins_size
        self.read_len = param.read_len
        self.contamination_ratio = param.contamination_ratio
        self.contamination_mean = param.contamination_mean
        self.contamination_stddev = param.contamination_stddev
        self.ctgs = []
        for i,length in enumerate(ctg_lengths):
            self.ctgs.append(Contig(i, length))
        self.ctgs = tuple(self.ctgs)
        self.gaps = [0]*(len(ctg_lengths)-1) # n contigs has n-1 gaps between them, start with gap size 0  
        
        # get positions for when all gaps are 0
        self.update_positions()

        # let us cheat here! Instead of calculating likeliooods of thousands of
        # onservations we calculate the ikelihood for them average (mean) of the
        # observations and weight it with the number of observations
        obs_dict = {}
        for c1,c2,is_PE_link in observations:
            nr_obs = len(observations[(c1,c2,is_PE_link)])
            if is_PE_link:
                PE_obs = map(lambda x: self.ctgs[c1].length + self.ctgs[c2].length - x + 2*param.read_len ,observations[(c1,c2,is_PE_link)])
                mean_obs = sum( PE_obs)/nr_obs
                obs_dict[(c1,c2,is_PE_link)] = (mean_obs,nr_obs)
                if mean_obs > self.contamination_mean + 6 * self.contamination_stddev and not initial_path:
                    self.observations = None
                    return None
            else:
                mean_obs = sum(observations[(c1,c2,is_PE_link)])/nr_obs
                obs_dict[(c1,c2,is_PE_link)] = (mean_obs,nr_obs)
            

        self.observations = obs_dict
        #print self.observations

        # for c1,c2 in self.observations:
        #     if self.observations[(c1,c2)][0] > 1500:
        #         print self.observations


    def get_distance(self,start_index,stop_index):
        total_contig_length = sum(map(lambda x: x.length ,filter(lambda x: start_index <= x.index < stop_index, self.ctgs) ))
        total_gap_length = sum(self.gaps[start_index:stop_index])
        index_adjusting = len(self.gaps[start_index:stop_index]) # one extra bp shifted each time
        return (total_contig_length, total_gap_length, index_adjusting)

    def update_positions(self):
        for ctg in self.ctgs:
            index = ctg.index
            ctg.position = sum(self.get_distance(0,index))

    def get_inferred_isizes(self):
        self.isizes = {}

        for (c1,c2,is_PE_link) in self.observations:
            gap = self.ctgs[c2].position - (self.ctgs[c1].position + self.ctgs[c1].length) - (c2-c1) # last thing is an index thing
            #x = map(lambda obs: obs[0] + gap , self.observations[(c1,c2)]) # inferr isizes
            x = self.observations[(c1,c2,is_PE_link)][0] + gap
            self.isizes[(c1,c2,is_PE_link)] = x

    def get_GapEst_isizes(self):
        self.gapest_predictions = {}
        for (i,j,is_PE_link) in self.observations:
            mean_obs = self.observations[(i,j,is_PE_link)][0]
            if is_PE_link:
                self.gapest_predictions[(i,j,is_PE_link)] = self.observations[(i,j,is_PE_link)][0] + GC.GapEstimator(self.contamination_mean, self.contamination_stddev, self.read_len, mean_obs, self.ctgs[i].length, self.ctgs[j].length)
            else:
                self.gapest_predictions[(i,j,is_PE_link)] = self.observations[(i,j,is_PE_link)][0] + GC.GapEstimator(self.mean, self.stddev, self.read_len, mean_obs, self.ctgs[i].length, self.ctgs[j].length)

            #print mean_obs, self.ctgs[i].length, self.ctgs[j].length, 'gap:' ,  GC.GapEstimator(self.mean, self.stddev, self.read_len, mean_obs, self.ctgs[i].length, self.ctgs[j].length)
        


    def new_state_for_ordered_search(self,start_contig,stop_contig,mean,stddev):
        """
            This function gives a new state between two contigs c1 and c2 in the contig path
            that we will change the gap size for.
            Currently, we change the gap to expected_mean_obs - mean_observation, e.g. a
            semi-naive estimation. More variablity in gap prediction could be acheved in the 
            same way as described for function propose_new_state_MCMC().
             
        """
        new_path = copy.deepcopy(self) # create a new state
        (c1,c2) = (start_contig,stop_contig)
        mean_obs = self.observations[(c1,c2,0)][0] if (c1,c2,0) in self.observations else self.observations[(c1,c2,1)][0]  # take out observations and
        exp_mean_over_bp = mean + stddev**2/float(mean+1)
        proposed_distance = exp_mean_over_bp - mean_obs # choose what value to set between c1 and c2 

        #print 'CHOSEN:', (c1,c2), 'mean_obs:', mean_obs, 'proposed distance:', proposed_distance
        (total_contig_length, total_gap_length, index_adjusting) = self.get_distance(c1+1,c2)
        #print 'total ctg length, gap_lenght,index adjust', (total_contig_length, total_gap_length, index_adjusting)
        avg_suggested_gap = (proposed_distance - total_contig_length) / (c2-c1)
        #print avg_suggested_gap, proposed_distance, total_contig_length, c2-c1
        for index in range(c1,c2):
            new_path.gaps[index] = avg_suggested_gap
        #new_path.gaps[index] = proposed_distance
        return new_path


    # def calc_log_likelihood(self,mean,stddev):
    #     log_likelihood_value = 0
    #     exp_mean_over_bp = mean + stddev**2/float(mean+1)
    #     for (c1,c2) in self.isizes:
    #         log_likelihood_value += math.log( normpdf(self.isizes[(c1,c2)],exp_mean_over_bp, stddev) ) * self.observations[(c1,c2)][1]
    #         #for isize in self.isizes[(c1,c2)]:
    #         #    log_likelihood_value += math.log( normpdf(isize,mean,stddev) )
            
    #     return log_likelihood_value

    def calc_dist_objective(self):
        objective_value = 0
        self.get_GapEst_isizes()
        for (c1,c2, is_PE_link) in self.isizes:
            objective_value += abs(self.gapest_predictions[(c1,c2,is_PE_link)] - self.isizes[(c1,c2,is_PE_link)]) * self.observations[(c1,c2,is_PE_link)][1]
            #for isize in self.isizes[(c1,c2)]:
            #    objective_value += math.log( normpdf(isize,mean,stddev) )
            
        return objective_value

    def make_path_dict_for_besst(self):
        path_dict = {}
        for ctg1,ctg2 in zip(self.ctgs[:-1],self.ctgs[1:]):
            path_dict[(ctg1,ctg2)] = ctg2.position - (ctg1.position + ctg1.length) - 1 
        #print path_dict
        return path_dict


    def calc_probability_of_LP_solution(self, help_variables):
        log_prob = 0
        for (i,j,is_PE_link),variable in help_variables.iteritems():
            print self.contamination_ratio * self.observations[(i,j,is_PE_link)][1] * normpdf(variable.varValue,self.contamination_mean,self.contamination_stddev)
            if is_PE_link:
                try:
                    log_prob += math.log(self.contamination_ratio * self.observations[(i,j,is_PE_link)][1] * normpdf(variable.varValue,self.contamination_mean,self.contamination_stddev)) 
                except ValueError:
                    log_prob += - float("inf")
            else:
                try:
                    log_prob += math.log((1 - self.contamination_ratio) * self.observations[(i,j,is_PE_link)][1]* normpdf(variable.varValue,self.contamination_mean,self.contamination_stddev)) 
                except ValueError:
                    log_prob += - float("inf")
        return log_prob

    def LP_solve_gaps(self):
        exp_means_gapest = {}

        for (i,j,is_PE_link) in self.observations:
            mean_obs = self.observations[(i,j,is_PE_link)][0]
            if is_PE_link:
                exp_means_gapest[(i,j,is_PE_link)] = self.observations[(i,j,is_PE_link)][0] + GC.GapEstimator(self.contamination_mean, self.contamination_stddev, self.read_len, mean_obs, self.ctgs[i].length, self.ctgs[j].length)
                #print 'GAPEST:',mean_obs, self.ctgs[i].length, self.ctgs[j].length, 'gap:' ,  GC.GapEstimator(self.contamination_mean, self.contamination_stddev, self.read_len, mean_obs, self.ctgs[i].length, self.ctgs[j].length)

            else:
                exp_means_gapest[(i,j,is_PE_link)] = self.observations[(i,j,is_PE_link)][0] + GC.GapEstimator(self.mean, self.stddev, self.read_len, mean_obs, self.ctgs[i].length, self.ctgs[j].length)
                #print 'GAPEST:',mean_obs, self.ctgs[i].length, self.ctgs[j].length, 'gap:' ,  GC.GapEstimator(self.mean, self.stddev, self.read_len, mean_obs, self.ctgs[i].length, self.ctgs[j].length)
        

        #exp_mean_over_bp = self.mean + self.stddev**2/float(self.mean+1)

        #calculate individual exp_mean over each edge given observation with gapest??

        gap_vars= []
        for i in range(len(self.ctgs)-1):
            gap_vars.append( LpVariable(str(i), None, self.mean + 2*self.stddev, cat='Integer'))

        # help variables because objective function is an absolute value
        help_variables = {}
        for (i,j,is_PE_link) in self.observations:
            help_variables[(i,j,is_PE_link)] = LpVariable("z_"+str(i)+'_'+str(j)+'_'+str(is_PE_link), None, None,cat='Integer')

        # # variables to penalize negative gaps
        # penalize_variables = {}
        # for i in range(len(self.gaps)):
        #     penalize_variables[i] =  LpVariable("r_"+str(i)+'_'+str(j), None, 0,cat='Integer')

        # PENALIZE_CONSTANT = Counter()
        # for (i,j) in self.observations:
        #     for k in range(i,j-1): # all gaps between contig i and j
        #         # we penalize with the count of all observations spanning over the gap
        #         PENALIZE_CONSTANT[k] += self.observations[(i,j)][1] # number of observations spanning over the gap 

        problem = LpProblem("PathProblem",LpMinimize)

        #problem += lpSum( [ help_variables[(i,j,is_PE_link)]*self.observations[(i,j,is_PE_link)][1] for (i,j,is_PE_link) in self.observations] ) , "objective"
        if self.contamination_ratio:
            problem += lpSum( [ is_PE_link * (1 - self.contamination_ratio) * help_variables[(i,j,is_PE_link)]*self.observations[(i,j,is_PE_link)][1] + (1-is_PE_link)*(self.contamination_ratio)* help_variables[(i,j,is_PE_link)]*self.observations[(i,j,is_PE_link)][1] for (i,j,is_PE_link) in self.observations] ) , "objective"
        else:
            problem += lpSum( [ help_variables[(i,j,is_PE_link)]*self.observations[(i,j,is_PE_link)][1] for (i,j,is_PE_link) in self.observations] ) , "objective"

        # problem += lpSum( [ - penalize_variables[i]*PENALIZE_CONSTANT[i] for i in range(len(self.gaps))] )

        # adding constraints induced by the absolute value of objective function
        for (i,j,is_PE_link) in self.observations:
            problem += exp_means_gapest[(i,j,is_PE_link)] - sum(map(lambda x: x.length, self.ctgs[i+1:j])) - self.observations[(i,j,is_PE_link)][0] - lpSum( gap_vars[i:j] )  <= help_variables[(i,j,is_PE_link)] ,  "helpcontraint_"+str(i)+'_'+ str(j)+'_'+str(is_PE_link)

        for (i,j,is_PE_link) in self.observations:
            problem += - exp_means_gapest[(i,j,is_PE_link)] + lpSum( gap_vars[i:j] ) + sum(map(lambda x: x.length, self.ctgs[i+1:j])) + self.observations[(i,j,is_PE_link)][0]  <= help_variables[(i,j,is_PE_link)] ,  "helpcontraint_negative_"+str(i)+'_'+ str(j)+'_'+str(is_PE_link)


        # adding distance constraints

        # for (i,j,is_PE_link) in self.observations:
        #     # print 'order:',(i,j,is_PE_link)
        #     if is_PE_link:
        #         problem += lpSum( gap_vars[i:j] ) + sum(map(lambda x: x.length, self.ctgs[i+1:j])) + self.observations[(i,j,is_PE_link)][0]  <= self.contamination_mean +4*self.contamination_stddev,  "dist_constraint_"+str(i)+'_'+ str(j)+'_'+str(is_PE_link)                
        #     else:
        #         problem += lpSum( gap_vars[i:j] ) + sum(map(lambda x: x.length, self.ctgs[i+1:j])) + self.observations[(i,j,is_PE_link)][0]  <= self.mean +4*self.stddev,  "dist_constraint_"+str(i)+'_'+ str(j)+'_'+str(is_PE_link)

        # for (i,j,is_PE_link) in self.observations:
        #     if is_PE_link:
        #         problem += - lpSum( gap_vars[i:j] ) - sum(map(lambda x: x.length, self.ctgs[i+1:j])) - self.observations[(i,j,is_PE_link)][0]  <= - self.contamination_mean +4*self.contamination_stddev,  "dist_constraint_negative_"+str(i)+'_'+ str(j)+'_'+str(is_PE_link)
        #     else:
        #         problem += - lpSum( gap_vars[i:j] ) - sum(map(lambda x: x.length, self.ctgs[i+1:j])) - self.observations[(i,j,is_PE_link)][0]  <= - self.mean +4*self.stddev,  "dist_constraint_negative_"+str(i)+'_'+ str(j)+'_'+str(is_PE_link)

        # # Adding constraints induced from introducing a negative gap penalizer
        # for i in range(len(self.gaps)):
        #     problem += penalize_variables[i] - gap_vars[i]  <= 0 ,  "neg_gap_contraint_"+str(i)


        try:
            problem.solve()
        except : #PulpSolverError:
            problem.solve()
            print 'Could not solve LP, printing instance:'
            print 'Objective:'
            print problem.objective
            print 'Constraints:'
            print problem.constraints

            print 'Solving with ordered_search instead'
            path = ordered_search(self)
            return path.gaps

        optimal_gap_solution = [0]*(len(self.ctgs) -1)
        for v in problem.variables():
            try:
                optimal_gap_solution[int( v.name)] = v.varValue
                #print v.name, "=", v.varValue,
            except ValueError:
                #print v.name, "=", v.varValue,
                pass

        self.objective = value(problem.objective)
        #prob = self.calc_probability_of_LP_solution(help_variables)

        #print self.objective
        #print optimal_gap_solution
        return optimal_gap_solution

    def __str__(self):
        string= ''
        for ctg in self.ctgs:
            string += 'c'+str(ctg.index) +',startpos:'+ str(ctg.position)+',endpos:'+str(ctg.position + ctg.length)+'\n'

        return string

        
def ordered_search(path):

    path.get_inferred_isizes()

    for c1 in range(len(path.ctgs)-1):
        for c2 in range(c1+1,len(path.ctgs)):
            if (c1,c2) in path.observations:
                suggested_path = path.new_state_for_ordered_search(c1,c2,path.mean,path.stddev)
                suggested_path.update_positions()
                suggested_path.get_inferred_isizes()
                suggested_path.calc_dist_objective()
                if suggested_path.calc_dist_objective() < path.calc_dist_objective():
                    #print "SWITCHED PATH TO SUGGESTED PATH!!"
                    path = suggested_path
                    #print path
                else:
                    pass
                    #print 'PATH not taken!'
            else:
                continue
      
    # print 'FINAL PATH:'
    # print path
    # print 'With likelihood: ', path.calc_log_likelihood(mean,stddev)  

    return path

def main(contig_lenghts, observations, param, initial_path):
    """
    contig_lenghts: Ordered list of integers which is contig lengths (ordered as contigs comes in the path)
    observations:  dictionary oflist of observations, eg for contigs c1,c2,c3
                    we can have [(c1,c2):[23, 33, 21],(c1,c3):[12,14,11],(c2,c3):[11,34,32]]
    """

    path = Path(contig_lenghts,observations, param, initial_path=initial_path)
    
    if path.observations == None:
        return None

    # ML_path = MCMC(path,mean,stddev)
    # ML_path = ordered_search(path,mean,stddev)

    optimal_LP_gaps = path.LP_solve_gaps()
    path.gaps = optimal_LP_gaps
    path.update_positions()

    # print 'MCMC path:'
    # print ML_path
    # print 'MCMC path likelihood:',ML_path.calc_log_likelihood(mean,stddev)
    # print 'Ordered search path:'
    # print ML_path
    # print 'Ordered search likelihood:',ML_path.calc_log_likelihood(mean,stddev)
    # return ML_path

    return path

if __name__ == '__main__':
    contig_lenghts = [3000,500,500,500,3000]
    observations_normal = {(0,1):[1800,2000], (0,2):[1500,1800,1400,1700], (0,3):[1200,800,1000], 
                    (1,2):[750,800],(1,3):[600,700], (1,4):[300,600,700],
                    (2,3):[700,750], (2,4):[1400,1570],
                    (3,4):[2000,1750], }

    observations_linear = {(0,1):[450,500],  (1,2):[750,800],
                    (2,3):[700,750], (3,4):[400,170]}

    # The imortance of support:
    # one edge with a lot of observatins contradicting the others
    observations_matter = {(0,1):[450,500],  (1,2):[750,800],
                    (2,3):[700,750], (3,4):[400,170],
                    (0,4):[700]*30 }

    # long contig, 500bp gap, 3*short_contigs, long contig
    observations = {(0,1):[1450,1300,1200,1570], (0,2):[700,800,1000], (0,3):[250,300],
                    (1,2):[900],(1,3):[900,800], (1,4):[800,900,1000],
                    (2,3):[900], (2,4):[900,800],
                    (3,4):[1500,1750,1350,1900,1950] }

    # negative gaps test case here:

 
    mean = 1500
    stddev = 500
    read_len = 100
    main(contig_lenghts,observations,mean,stddev, read_len)


