# post-fMRIPrep-analysis
Updated version of "https://github.com/poldracklab/ds003-post-fMRIPrep-analysis/tree/" python scripts for FMRI brain imaging analysis

## Getting Started

Clone or download the repository

```
git clone https://github.com/joshjzhou/post-fMRIPrep-analysis.git
```

### Installing

Install dependencies
```
pip install -r requirements.txt
```
## Customize

Update the contrast in workflows.py in line 56. An example contrast looks like: contrasts=[('intask', 'T', ['word', 'pseudoword'], [1, 1])].

## File Structure
The file structure should follow the BIDS standard format. An example file structure looks as follows:
- run.py
- interfaces.py
- workflows.py 
- Preprocessed (folder)
---- fmriprep (folder) <--- this is the derivatives directory
-------- sub-(number) (folder)
------------ anat (folder)
------------ figures (folder)
------------ ses-post (folder)
------------ ses-pre (folder)
- Nifti (folder) <--- this is the bids directory
---- sub-(number) (folder)
-------- ses-pre
-------- ses-post

## Run the application

Run the following command to start the analysis
```
python [relative filepath of run.py] [relative filepath of the derivatives directory] [relative filepath of the output folder] [analysis level (either participant or group)] --space [specific space to be processed] --bids-dir [relative filepath of bids directory] --participant-label [can be a single participant or multiple] --task [type of task] -w [relative filepath where intermediate documents should be stored]
```
Example command:
```
python run.py "preprocessed/fmriprep" "results" participant --space MNI152NLin2009cAsym --bids-dir "Nifti" --participant-label 104 119 --task nback -w "work"
```
Everything in quotes is a folder.

## Possible Errors

You'll likely need to install additional modules such as fsl. The updated run.py has not yet been tested with group level analysis, so if you do run into any errors, please feel free to contact me.


