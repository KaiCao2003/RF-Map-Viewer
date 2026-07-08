function MasterFile_EB_VisualStimuli(choice)
 % function MasterFile_EB_VisualStimuli()
    cfg = VisStimConfig();
    addpath(genpath(cfg.fmaToolboxRoot));
    % ======= Choice Selection =======
    choiceStr = sprintf('do_choice%02d', 3);
    if exist(choiceStr, 'file') || exist(choiceStr, 'builtin') || exist(choiceStr, 'local')
        D = feval(choiceStr);  % Call the function
        disp(D)                % Display output (optional)
    else
        error('Choice %d does not correspond to a defined function.', choice);
    end
end

%% ======= Path Control Center =======

function cfg = VisStimConfig()
cfg.gratingStimFile = '';

% Edit this block when moving the analysis to a new computer or session.

% cfg.choice = 3;

cfg.sessionFolder = '260603_14';
cfg.probeLetter = 'B';
cfg.session = [cfg.sessionFolder,'_Probe',cfg.probeLetter];
oneBoxStream = 'OneBox-103';

cfg.gratingSpreadsheetRow = 10;
cfg.rfSpreadsheetRow = 1;

resfile_folder_path = 'R:\';
date = '260603\';

data_folder = 'data\260603\260603_4\';

recording_folder = [data_folder, date, 'Record Node 102\'];
pipeline_folder = [data_folder, 'pipeline/', cfg.sessionFolder, '/Probe', cfg.probeLetter, '/'];
cfg.rfStimFile = [data_folder, '260603.mat'];
cfg.adcConcatRawFile = [data_folder, 'pipeline/', cfg.sessionFolder, '/OneBox-ADC/concat/traces_cached_seg0.raw'];



% recording_folder = 'data\VisualStimuli\AA005_ADn_2026-02-12_14-31-05_VisualStimuli\Record Node 125\';
% pipeline_folder = ['data\VisualStimuli\pipeline\Probe', cfg.probeLetter, '\kilosort4_output\'];
% cfg.rfStimFile = ['data\VisualStimuli\rfmapping.mat'];
% cfg.adcConcatRawFile = 'data\VisualStimuli\pipeline\OneBox-ADC\concat\traces_cached_seg0.raw';



kilosort_folder = [pipeline_folder, 'kilosort\'];
probe_concat_folder = [pipeline_folder, 'concat\'];

cfg.outputMatFilesFolder = 'R:\Kai\MatlabApps\MatFiles';

cfg.gratingSpreadsheetFile = [resfile_folder_path, 'Kai\MatlabApps\360\Info_Grating.xlsx'];
cfg.rfSpreadsheetFile = [resfile_folder_path, 'Kai\MatlabApps\360\Info_RFmapping.xlsx'];


cfg.settingsFile = [recording_folder, 'settings.xml'];

cfg.clusterKSLabelFile = [kilosort_folder, 'cluster_KSLabel.tsv'];
cfg.clusterGroupFile = [kilosort_folder, 'cluster_group.tsv'];
cfg.spikeClustersFile = [kilosort_folder, 'spike_clusters.npy'];
cfg.spikeTimesFile = [kilosort_folder, 'spike_times.npy'];

cfg.adcSpikeTimesFile = cfg.spikeTimesFile;

cfg.probeConcatRawFile = [probe_concat_folder, 'traces_cached_seg0.raw'];

cfg.adcContinuousFile = [recording_folder, 'experiment1\recording1\continuous\', oneBoxStream, '.OneBox-ADC\continuous.dat'];
cfg.adcContinuousSampleNumbersFile = [recording_folder, 'experiment1\recording1\continuous\', oneBoxStream, '.OneBox-ADC\sample_numbers.npy'];
cfg.adcTtlTimestampsFile = [recording_folder, 'experiment1\recording1\events\', oneBoxStream, '.OneBox-ADC\TTL\timestamps.npy'];
cfg.adcTtlSampleNumbersFile = [recording_folder, 'experiment1\recording1\events\', oneBoxStream, '.OneBox-ADC\TTL\sample_numbers.npy'];

cfg.probeContinuousFile = [recording_folder, 'experiment1\recording1\continuous\', oneBoxStream, '.Probe', cfg.probeLetter, '\continuous.dat'];
cfg.probeContinuousSampleNumbersFile = [recording_folder, 'experiment1\recording1\continuous\', oneBoxStream, '.Probe', cfg.probeLetter, '\sample_numbers.npy'];
cfg.probeTtlTimestampsFile = [recording_folder, 'experiment1\recording1\events\', oneBoxStream, '.Probe', cfg.probeLetter, '\TTL\timestamps.npy'];
cfg.probeTtlSampleNumbersFile = [recording_folder, 'experiment1\recording1\events\', oneBoxStream, '.Probe', cfg.probeLetter, '\TTL\sample_numbers.npy'];

% Add rows here only if prec_files is non-empty in the spreadsheet.
% Columns: session folder, file key, literal file path.
cfg.precedingSessionFiles = cell(0,3);
cfg.fmaToolboxRoot = [resfile_folder_path, 'Kai\MatlabApps\buzcode\externalPackages\FMAToolbox'];

end

%% ======= Available Choices =======

function D = do_choice14 % Create new cluster labels & compute mean waveforms
% Update cluster_KSLabel.tsv (from Kilosort) using cluster_group.tsv (from
% Phy). Can then avoid manually labelling every unit in Phy.

%% Setup
% session = 'Mouse08_20251007_795to2220';
cfg = VisStimConfig();
session = cfg.session;
settingsFile = VisStimSettingsFile();

guo = true; % good units only
resfiles = true; % Load data from resfiles
load_data_manually = false; % Opens File Explorer for you to select path to data
manual_curation = false; % false if labels not curated in Phy
%%

probe_letter = 'A';
if contains(session,'Probe')
    tok = regexp(session, 'Probe([A-Za-z])', 'tokens');
    probe_letter = tok{1}{1};
end

if load_data_manually
    disp('Please select kilosort folder')
    ks_path = uigetdir('');

    if ks_path == 0
        disp('User canceled');
    end
    disp('Please select concat file')
    [cf, cp] = uigetfile('*.raw');        
    if isequal(cf,0)
        disp('User canceled');
    else
        concat_f = fullfile(cp, cf);
    end

    % Alternatively put manual paths here.

    nchan = 384;

else
    ks_path = VisStimProbePath(probe_letter,'kilosort');
    concat_f = VisStimInputFile('probe_concat_raw');
    nchan = 384;
end

lf = VisStimMatFile('New_Unit_Labels',[session,'.mat']);
EnsureParentDir(lf);

% 1. Make new unit labels
if ~(exist(lf,'file')==2)
    ksl_f = VisStimInputFile('cluster_KSLabel');
    cg_f = VisStimInputFile('cluster_group');
    
    ksl = readtable(ksl_f, 'FileType', 'text', 'Delimiter', '\t');
    cg = readtable(cg_f, 'FileType', 'text', 'Delimiter', '\t');
    new_labels = ksl;

    if manual_curation    
        edited_idx = arrayfun(@(c) find(ksl.cluster_id==cg.cluster_id(c)),...
            1:height(cg), 'UniformOutput', false);
        
        e = cellfun(@isempty, edited_idx);
        edited_idx = cell2mat(edited_idx(~e));
        
        % Give new_label the manually curated labels from Phy 
        new_labels.KSLabel(edited_idx) = cg.group(~e);
        
        % Add extra (new units) to new_label from cluster_group
        n_new_units = length(find(e));
        if n_new_units > 0
            first_new_id = cg.cluster_id(find(e,1));
            start = find(new_labels.cluster_id < first_new_id, 1, 'last')+1;
            move_to_end = new_labels(start:end,:);
            
            new_labels(start:start+n_new_units-1,:) = cg(find(e),:);
            start = height(new_labels)+1;
            new_labels(start:start+height(move_to_end)-1,:) = move_to_end;
        end
    end
    
    save(lf,'new_labels');
end

% 2. Compute mean waveforms from raw data
% DepthSort_meanWaveForms(ks_path,concat_f,settingsFile,384,guo,nchan,1)
DepthSort_meanWaveForms(ks_path,concat_f,session,settingsFile,384,guo,nchan,1)

% Plot waveforms
wf_p = VisStimMatFile('SpikeWaveforms','mean_wfs',[session,'.mat']);
load(wf_p)

m = meanWaveforms;

figure
sel = 1:25;
pos = 1;
for i = sel
    subplot(5,5,pos)
    ch_i = m.ptp_chan_idx(i);
    uid = num2str(m.unitIds(i));
    chan = num2str(m.chanmap(ch_i));

    plot(m.timepts, m.data(ch_i,:,i))
    spt = ['uid: ',uid,', ch: ',chan];
    title(spt)

    pos = pos+1;
end

D = 'Finished do_choice14';
end

function D = do_choice02 % Sync full-field grating data & show summary figures
    disp('Choice 02: Sync full-field grating data and show summary figures');

    %% Input: File_Info_Grating row number
    cfg = VisStimConfig();
    rn = cfg.gratingSpreadsheetRow;  
    mode_sel = 'mean';
    use_events = true; % Use sample_numbers.npy from events folder instead of continuous data
    sync2adc = false; % Detect PD pulse times from concatenated ADC file, then use adc_spike_times.npy
    guo = true;
    resfiles = false;
    load_data_manually = false;
    %%
    tb = readtable(VisStimSpreadsheetFile('Info_Grating.xlsx'));   
    session_folder = tb.session_folder{rn};
    session = tb.Session{rn};
    probe_letter = 'A';
    if contains(session,'Probe')
        tok = regexp(session, 'Probe([A-Za-z])', 'tokens');
        probe_letter = tok{1}{1};
    end
        
    if load_data_manually
        disp('Please select kilosort folder')
        ks_path = uigetdir('');    
        if ks_path == 0
            disp('User canceled');
        end

        disp('Please select Psychtoolbox file')
        [pf, pp] = uigetfile('*.mat');    
        if isequal(pf,0)
            disp('User canceled');
        else
            stim_path = fullfile(pp, pf);
        end

        disp('Please select Probe settings file')
        [sf, sp] = uigetfile('*.xml');        
        if isequal(sf,0)
            disp('User canceled');
        else
            settingsFile = fullfile(sp, sf);
        end

        % Alternatively put manual paths here.

        fn = session;

    else
        ks_path = VisStimProbePath(probe_letter,'kilosort');
        stim_path = VisStimStimFile('grating');
        
        settingsFile = VisStimSettingsFile(session_folder);
        fn = session;
    end

    % For Sync_Signals: Recordings preceding the file in stim_path 
    prec_files = tb.prec_files{rn}; 
    protocol = tb.Protocol{rn};

     fp = VisStimMatFile('Grating_data','Sync_Pulses',[fn,'.mat']);
     EnsureParentDir(fp);

    if exist(fp, 'file') == 2
        load(fp)
        load(stim_path)
    else
        [trials, Vq] = Sync_Signals(session_folder,ks_path,stim_path,prec_files,...
            settingsFile,protocol,[],use_events,sync2adc,probe_letter);
        save(fp,'Vq')
    end
    GratingAnalysis_EB(ks_path,fn,trials,Vq,mode_sel,settingsFile,...
        sync2adc,guo)   
    D = 'Finished do_choice02';
end

function D = do_choice03 % Sync RF mapping data & construct maps 
    disp('Choice 03: Sync RF mapping data and show summary figures');

    %% Input: File_Info_RFmapping row number
    cfg = VisStimConfig();
    rn = cfg.rfSpreadsheetRow;
    mode_sel = 'mean'; % default: 'sum'
    nbins = 25;
    save_figs = true;
    save_gauss = true;
    use_events = true; % Use samplenumbers.npy from events folder instead of continuous data
    sync2adc = false;
    guo = true;
    resfiles = false; % Access KS data from resfiles
    load_data_manually = false;
    %%
    tb = readtable(VisStimSpreadsheetFile('Info_RFmapping.xlsx'));   
    session_folder = cfg.sessionFolder;    
    gdp = VisStimMatFile('RF_maps','Gaussian_Data');
    EnsureDir(gdp);
    gdf = fullfile(gdp,[VisStimFileStem(session_folder),'.mat']);
    gdi = [extractBefore(gdf,'.mat'),'_idx','.mat'];
    session = cfg.session;
    probe_letter = 'A';
    if contains(session,'Probe')
        tok = regexp(session, 'Probe([A-Za-z])', 'tokens');
        probe_letter = tok{1}{1};
    end

    if load_data_manually
        disp('Please select kilosort folder')
        ks_path = uigetdir('');    
        if ks_path == 0
            disp('User canceled');
        end

        disp('Please select Psychtoolbox file')
        [pf, pp] = uigetfile('*.mat');    
        if isequal(pf,0)
            disp('User canceled');
        else
            stim_path = fullfile(pp, pf);
        end

        disp('Please select Probe settings file')
        [sf, sp] = uigetfile('*.xml');        
        if isequal(sf,0)
            disp('User canceled');
        else
            settingsFile = fullfile(sp, sf);
        end

        % Alternatively put manual paths here.

    else
        stim_path = VisStimStimFile('rfmapping');
        ks_path = VisStimProbePath(probe_letter,'kilosort');
        settingsFile = VisStimSettingsFile(session_folder);

    end
    
    fp = VisStimMatFile('RF_maps','Sync_Pulses',[session,'.mat']);
    EnsureParentDir(fp);
    % For Sync_Signals: Recordings preceding the file in stim_path 
    prec_files = tb.prec_files(rn);  
    protocol = tb.Protocol{rn};
    
    if exist(fp, 'file')==2
        load(fp)
        load(stim_path)
    else
        [trials, Vq] = Sync_Signals(session_folder,ks_path,stim_path,prec_files,...
            settingsFile,protocol,[],use_events,sync2adc,probe_letter);
         save(fp,'Vq')
    end

    if exist(gdf,'file')==2
        load(gdf)
    else
        gauss_2d_data = [];
    end
    
    RFmapping_EB(ks_path,session,trials,Vq,mode_sel,nbins,save_figs,...
        save_gauss,gauss_2d_data,gdi,settingsFile,sync2adc,guo)
    D = 'Finished do_choice03';
end

%% ====== Functions ======

function [stimInfo, Vq] = Sync_Signals(cf,ks_path,stim_path,prec_files,...
    settingsFile,protocol,stim_name,use_events,sync2adc,probe_letter)
    close all    
    % Synchronizing data using function generator
    % ADC channel 0: Function generator signal
    % ADC channel 1: Photodiode signal
    % Probe channel 385: Function generator signal
    % FG: Function generator, PD: Photodiode
    
    sr = 30000; 
    sr_adc = 30300.5;
    nchan_probe = 385;
    nchan_adc = 12;
    
    % Stimulus file:
    load(stim_path);
    if exist('trials','var')
        stimInfo = trials;
    end
    
    % Load data streams first to find the shortest recording duration (probably 
    % Probe data) over which to align the signals
    
    d0 = LoadBinary(VisStimRawFile(cf,'adc_continuous'),...
        'frequency',sr_adc,'nChannels',12,'channels',1);
    d0_dur = length(d0)/sr_adc;

    snd0_cont = readNPY(VisStimRawFile(cf,'adc_continuous_sample_numbers'));
    snd0_cont = double(snd0_cont);

    tsd0 = readNPY(VisStimRawFile(cf,'adc_ttl_timestamps'));
    snd0 = readNPY(VisStimRawFile(cf,'adc_ttl_sample_numbers'));
    snd0 = double(snd0);
    snd0 = snd0-snd0_cont(1);
    snd0t = snd0/sr_adc;

    if ~any(strcmp(protocol,{'RFmapping360','Grating360'}))
        d1_chan = 2; 
    else
        d1_chan = 4;
    end

    d1 = LoadBinary(VisStimRawFile(cf,'adc_continuous'),...
        'frequency',sr_adc,'nChannels',12,'channels',d1_chan);
    d1_dur = length(d1)/sr_adc; % recording duration (sec)
    
    if ~strcmp(protocol,'RFmapping360')
        d385 = LoadBinary(VisStimRawFile(cf,'probe_continuous'),...
        'frequency',sr,'nChannels',nchan_probe,'channels',385);
         d385_dur = length(d385)/sr;
    end
    snd385_cont = readNPY(VisStimRawFile(cf,'probe_continuous_sample_numbers'));
    snd385_cont = double(snd385_cont);

    tsd385 = readNPY(VisStimRawFile(cf,'probe_ttl_timestamps'));
    snd385 = readNPY(VisStimRawFile(cf,'probe_ttl_sample_numbers'));
    snd385 = double(snd385);
    snd385 = snd385-snd385_cont(1);
    snd385t = snd385/sr;

    % Get the duration (in seconds) of ADC0 and Probe channel 385 recordings 
    % in concatenated file, preceding FullFieldGrating, in order of recording.
    % The sum of these durations gives the start time of the FullFieldGrating
    % recording, relative to the concatenated file.    
           
    prec_files = VisStimText(prec_files);
    pfiles = split(prec_files,',');
    nfiles = length(pfiles);
    if isempty(prec_files) || strcmpi(prec_files,'NaN')
        nfiles = 0;
    end

    d0_ar = cell(nfiles,1);
    d385_ar = cell(nfiles,1);
    d0_dur_ar = zeros(nfiles,1);
    d385_dur_ar = zeros(nfiles,1);
    
    d0_dur_ar = zeros(nfiles,1);
    d0_ns_ar = zeros(nfiles,1);
    d385_dur_ar = zeros(nfiles,1);
    bytesPerSample = 2; % int16 = 2 bytes
 
    for ff = 1:nfiles
        fn = pfiles{ff};
        cf_prec = VisStimPrecedingSession(cf,fn);
        fp_d0_prec = VisStimRawFile(cf_prec,'adc_continuous');
        d0_info = dir(fp_d0_prec);
        d0_nSamples = d0_info.bytes / (nchan_adc * bytesPerSample); 
        d0_ns_ar(ff) = d0_nSamples;
        d0_dur_ar(ff) = d0_nSamples / sr_adc;
        
        fp_d385_prec = VisStimRawFile(cf_prec,'probe_continuous');
        d385_info = dir(fp_d385_prec);
        d385_nSamples = d385_info.bytes / (nchan_probe * bytesPerSample);
        d385_dur_ar(ff) = d385_nSamples / sr;
    end
    d0_start_time = sum(d0_dur_ar);
    d385_start_time = sum(d385_dur_ar);
    
    if sync2adc
        cadc_f = VisStimAdcConcatFile(ks_path);
        cadc = LoadBinary(cadc_f,'frequency',sr_adc,'nChannels',12,'channels',d1_chan);
        n_prec_samples = sum(d0_ns_ar);
        prec_sec = n_prec_samples/sr_adc;
        n_d1_samples = height(d1); 
        ws = n_prec_samples+1;
        wf = ws+n_d1_samples-1; 
        win = ws:wf;

        d1_cadc = double(cadc(win));
        td1_cadc = win/sr_adc;
        td1_cadc = td1_cadc';
    end

    %% Function generator signal sent to ADC channel 0 %%
    if ~use_events
        td0 = (1:length(d0))/sr_adc;
        td0 = td0';
        
        figure
        i0 = td0<=d1_dur;
        plot(td0(i0),d0(i0)), hold on
        % plot(td0,d0), hold on
        title('FG signal, ADC0')
        
        % periods: Timepoints of the rising & falling edge of each pulse
        [periods0,in0] = Threshold([td0,double(d0)],'>',10000,'min',0.2);
        d0_midpts = mean(periods0,2); 
        periods0_1col = reshape(periods0.', [], 1);
        
        sweep_time = 20; 
        ns = d1_dur/sweep_time;
        blocks1 = 0:sweep_time:d1_dur-sweep_time;
        blocks2 = sweep_time:sweep_time:d1_dur;
        
        % Function generator: Changes from 1-2 Hz over 20 sec
        nperiods = arrayfun(@(n) sum(periods0(:,1)>blocks1(n) & periods0(:,1)<=blocks2(n)),...
            1:ns, 'UniformOutput', false);
        
        p0_tb = table(periods0(:,1),periods0(:,2),periods0(:,2)-periods0(:,1),...
            'VariableNames',{'RisingEdge','FallingEdge','Difference'});
        
        nperiods = cell2mat(nperiods);
        bf = cumsum(nperiods);
        bs = [1,bf(1:end-1)+1];
        
        % New strategy: Leading edge is the first rising edge following the 
        % smallest width pulse in each 20s sweep. 
        
        [~,min_pts] = arrayfun(@(n) min(p0_tb.Difference(bs(n):bf(n))), 1:length(bs));
        min_pts = min_pts+bs-1;
        start_pts = min_pts+1; % Add 1: The next edge should be the largest pulse
        
        % For each start_pt, keep selecting the next pulse if it is larger
        for i = 1:length(start_pts)
            idx = start_pts(i);
            w = p0_tb.Difference(idx);
            while p0_tb.Difference(idx+1)>w || p0_tb.Difference(idx)<0.4 
                start_pts(i) = idx+1;
                idx = idx+1;
                w = p0_tb.Difference(idx);
            end
        end
    
        % Remove duplicate points:
        [~, ~, ic] = unique(start_pts, 'stable');
        [~, first_occurrence] = unique(ic, 'first');
        all_indices = 1:numel(start_pts);
        dup = setdiff(all_indices, first_occurrence);
        start_pts(dup) = [];
               
        st_adc0 = p0_tb.RisingEdge(start_pts); % Leading pulse rising edge start times
        et_adc0 = p0_tb.RisingEdge(start_pts(2:end)); % Leading pulse rising edge start times
        
        y1 = ones(length(st_adc0),1)*11000;
        plot(st_adc0,y1,'gx','MarkerSize',12)
    
        % Break here & uncomment code to fix edge detection errors:
        % Manually add last edge if missed
        % [x,y] = ginput(1); 
        % x = 1601.695;
        % st_adc0 = [st_adc0;x];
        % et_adc0 = [et_adc0;x];
    else
        st_adc0 = snd0t;
    end    
    
    %% Photodiode signal sent to ADC channel 1 %%
    td1 = (1:length(d1))/sr_adc;
    td1 = td1';

    figure
    plot(td1,d1), hold on
    title(sprintf('PD signal, ADC%d',d1_chan))
    
    if sync2adc
        figure
        plot(td1_cadc,d1_cadc), hold on
        title(sprintf('cADC PD signal, ADC%d',d1_chan))
        td1 = td1_cadc;
        d1 = d1_cadc;
    end
    
    % Threshold function output is: [rising edges, falling edges]
    % Grating: Appears that PD signal is 30 Hz. Width between starting and 
    % falling edge expected to be: 1/30/2 = 0.0167
    % 'min': minimum interval between rising and falling edge for pulse to
    % be included
    % 'max': Intervals between pulses < 'max' will be excluded

    switch protocol
        case 'Grating'
            % PD flickers at 30 Hz for 2 sec grating presentation, followed
            % by 2 sec gray screen
            py = 14000;
            [periods1,in1] = Threshold([td1,double(d1)],'>',py,'min',0.01);    
            % Remove any pulses with width < 1.5 s or > 2.5
            too_narrow = diff(periods1,1,2) < 1.5;
            periods1(too_narrow,:) = [];
            too_wide = diff(periods1,1,2) > 2.5;
            periods1(too_wide,:) = [];

            plot(periods1(:,1),ones(length(periods1),1)*py,'gx') 
            plot(periods1(:,2),ones(length(periods1),1)*py,'rx')

            periods1 = fliplr(periods1); 

            % Manually add the first falling edge
            % [x1,y] = ginput(1); 
            % x1 = 3833.96959;
            x1 = 3833.96959;

            % Manually add the last rising edge 
            % [x2,y] = ginput(1); 
            % x2 = 989.9629;
            x2 = 4809.77611;
            
            periods1 = [[x1;periods1(:,1)],[periods1(:,2);x2]];
            plot(periods1(:,1),ones(length(periods1),1)*py,'bo') 
            plot(periods1(:,2),ones(length(periods1),1)*py,'bo')

            nstim_frames = height(trials);

         case 'Grating360'
            % Sync patch flickers On & Off during 2 sec grating, then Off
            % for 2 sec gray interval. PD signal is overlaid on 60 Hz
            % refresh rate

            threshold = 15000;           
            isAboveThresholdMask = (d1 < threshold); 

            maxGapSamples1 = 100;
            maxGapSamples2 = 550; 
  
            figure
            plot(isAboveThresholdMask)
            ylim([-0.1,1.1])
            title('isAboveThresholdMask')

            figure
            plot(td1,isAboveThresholdMask)
            ylim([-0.1,1.1])
            title('isAboveThresholdMask')
 
            pd_noGap = fill_short_gaps(isAboveThresholdMask, maxGapSamples1);

            figure
            plot(pd_noGap)
            ylim([-0.1,1.1])
            title('pd_noGap')

            figure
            plot(td1,pd_noGap)
            ylim([-0.1,1.1])
            title('pd_noGap')

            pd_noGap_new = fill_short_gaps(~pd_noGap, maxGapSamples2);

            % pd_noGap_new = fill_short_gaps(pd_noGap_new, maxGapSamples);

            figure
            plot(pd_noGap_new), hold on
            ylim([-0.1 1.1])
            title('PD, processed signal')

            figure
            plot(td1, ~pd_noGap_new), hold on
            ylim([-0.1 1.1])
            title('PD, processed signal')

            py = 0.9;
            [periods1,in1] = Threshold([td1,double(~pd_noGap_new)],'>',py,'min',1);    
            % Remove any pulses with width < 1.5 s or > 2.5
            too_narrow = diff(periods1,1,2) < 1.3;
            periods1(too_narrow,:) = [];
            too_wide = diff(periods1,1,2) > 2.7;
            periods1(too_wide,:) = [];

            plot(periods1(:,1),ones(length(periods1),1)*py,'gx') % rising edges (grating onset)
            plot(periods1(:,2),ones(length(periods1),1)*py,'rx') % falling edges (grating offset)

            % Break here and do manual corrections

            p1 = periods1;
            % periods1 = p1(:,2);

            % Manually add the first falling edge
            % [x1,y] = ginput(1); 
            % xf = 3187.401;

            % periods1 = [xf;periods1];

            % Manually add the last rising edge 
            % [xr,y] = ginput(1); 
            % xr = 3922.045;

            % periods1 = [periods1,[p1(:,1);xr]];

            plot(periods1(:,1),ones(length(periods1),1)*py,'bo') 
            plot(periods1(:,2),ones(length(periods1),1)*py,'bo')

            nstim_frames = height(trials);
            dif = diff(periods1,1,2);
        
        case 'RFmapping'
            tf = 14000;
            tr = 2000;
            [pf,~] = Threshold([td1,double(d1)],'<',tf,'min',0.07);
            [pr,~] = Threshold([td1,double(d1)],'>',tr,'min',0.07);

            plot(pf,ones(length(pf),1).*tf,'gx') 
            plot(pr,ones(length(pr),1).*tr,'rx')
            
            wf = diff(pf,[],2);
            pf(wf>0.15,:) = [];
            wr = diff(pr,[],2);
            pr(wr>0.15,:) = [];

            plot(pf(:,1),ones(length(pf),1).*tf,'go') 
            plot(pr(:,1),ones(length(pr),1).*tr,'ro')  

            % Break here & uncomment code to fix edge detection errors:

            % Manually add the first falling edge if it is missed:
            % [x,y] = ginput(1); 
            % x = 13.6661;
            % periods1 = [NaN,x;periods1]; 
            % periods1_vector = periods1_vector(~isnan(periods1_vector));
        
            % Manually add the last falling edge if it is missed:
            % [x,y] = ginput(1); 
            % x = 951.8277;
            % periods1 = [periods1;x,NaN];

            % Add the last rising edge (if length(pr) == length(pf)-1): 
            % [x,y] = ginput(1);
            x = 3802.18362;
            pr = [pr;[x,NaN]];
           
            % Delete last falling edge:
            % pf = pf(1:end-1,:);

            nstim_frames = height(trials);
            periods1 = [pf(:,1),[pr(:,1)]];

            plot(periods1(:,1),ones(length(periods1),1).*tf,'bo') 
            plot(periods1(:,2),ones(length(periods1),1).*tr,'bo') 

        case 'RFmapping360'          
            threshold = 12000;           
            isAboveThresholdMask = (d1 < threshold);

            figure
            plot(isAboveThresholdMask), hold on
            ylim([-0.1 1.1])
            title('isAboveThresholdMask')

            figure
            plot(td1,isAboveThresholdMask), hold on
            ylim([-0.1 1.1])
            title('isAboveThresholdMask')

            maxGapSamples = 400;
            pd_noGap = fill_short_gaps(isAboveThresholdMask, maxGapSamples);            

            figure
            plot(td1,pd_noGap), hold on
            ylim([-0.1 1.1])
            title('pd_noGap')

            pd_noGap_inverted = ~pd_noGap;
            pd_noGap_new = fill_short_gaps(pd_noGap_inverted, 1000);
            pd_noGap_new = ~pd_noGap_new;

            figure
            plot(td1, pd_noGap_new), hold on
            ylim([-0.1 1.1])
            title('PD, processed signal')

            tf = 0.9;
            tr = 0.1;
            [pf,~] = Threshold([td1,double(pd_noGap_new)],'<',tf,'min',0.06);
            [pr,~] = Threshold([td1,double(pd_noGap_new)],'>',tr,'min',0.06);

            plot(pf,ones(length(pf),1).*tf,'gx') 
            plot(pr,ones(length(pr),1).*tr,'rx')

            % wf = diff(pf,[],2);
            % pf(wf>0.15,:) = [];
            % wr = diff(pr,[],2);
            % pr(wr>0.15,:) = [];

            % Manual editing:
            % Make cutoff to remove edges corresponding to next stimulus (grating)
            % cutoff = 2732;
            % pf_after_stim = pf(:,1) > cutoff;
            % pf(pf_after_stim,:) = [];
            % pr_after_stim = pr(:,1) > cutoff;
            % pr(pr_after_stim,:) = [];

            % % Remove time points before:
            % rb = 22;
            % pr_keep = pr(:,2) > rb;
            % pr = pr(pr_keep,:);
            % pf_keep = pf(:,1) >


            % 
            % % Remove time points after:
            % ra = 3500;
            % pr_keep = pr(:,1) < ra;
            % pr = pr(pr_keep,:);
            % pf_keep = pf(:,1) < ra;
            % pf = pf(pf_keep,:);

            pr = pr(2:end,:);

            periods1 = [pf(:,1),[pr(:,1)]];

            plot(pf(:,1),ones(length(pf),1).*tf,'bo') 
            plot(pr(:,1),ones(length(pr),1).*tr,'bo')              
            wf = diff(pf,[],2);
            wr = diff(pr,[],2);

            dif_periods1 = diff(periods1,1,2);
            dif_periods1 = [periods1,dif_periods1];

        case 'NaturalScenes' % MNIST or AllenScenes
            % Image display time: 250 ms
            tf = 14000;
            tr = 2000;
            [pf,~] = Threshold([td1,double(d1)],'<',tf,'min',0.2);
            [pr,~] = Threshold([td1,double(d1)],'>',tr,'min',0.2);

            plot(pf,ones(length(pf),1).*tf,'gx') 
            plot(pr,ones(length(pr),1).*tr,'rx')
            
            if ~strcmp(stim_name,'ImageNet')
                wf = diff(pf,[],2);
                pf(wf>0.28,:) = [];
                wr = diff(pr,[],2);
                pr(wr>0.28,:) = [];
            end

            plot(pf(:,1),ones(length(pf),1).*tf,'go') 
            plot(pr(:,1),ones(length(pr),1).*tr,'ro')

            pf = pf(:,1);
            pr = pr(:,1);

            % Manual correction (MNIST, AllenScenes)
            % Add the last falling & rising edge:

            % periods1 = [pf,pr]; 
            % % nstim_frames = size(stimInfo.image_order,1)*size(stimInfo.image_order,2);
            % 
            % % [xf,yf] = ginput(1); 
            % xf = 343.863;
            % % xf = 625.5587;
            % % [xr,yr] = ginput(1);
            % xr = 344.210;
            % % xr = 625.8698;
            % 
            % pf = [pf;xf];
            % pr = [pr;xr];

            % Manual correction (ImageNet)
            % pr = pr(3:end,:);
            % pf = pf(2:end,:);
            
            periods1 = [pf,pr];
            plot(pf,ones(length(pf),1).*tf,'bo') 
            plot(pr,ones(length(pr),1).*tr,'bo')
        
        case 'Movies'
            % Allen movies frame rate: 30 Hz. Image display time: ~33 ms
            % LOC movie: 24 Hz
            tf = 15000;
            tr = 2000;
            % min_ft = 0.02; % Allen movies
            min_ft = 0.02; % LOC movie
            [pf,~] = Threshold([td1,double(d1)],'<',tf,'min',min_ft);
            [pr,~] = Threshold([td1,double(d1)],'>',tr,'min',min_ft);

            plot(pf,ones(length(pf),1).*tf,'gx') 
            plot(pr,ones(length(pr),1).*tr,'rx')
            
            % For Allen movies:
            % wf = diff(pf,[],2);
            % pf(wf>0.07,:) = [];
            % wr = diff(pr,[],2);
            % pr(1:2,:) = [];

            % For LOC movie:
            pr = pr(:,1);
            pf = pf(:,1);
            pr([1,2]) = [];
            pf(1) = [];

            plot(pf(:,1),ones(length(pf),1).*tf,'go') 
            plot(pr(:,1),ones(length(pr),1).*tr,'ro')
            periods1 = [pf,pr];               
    end  
        
    % d1_midpts = mean(periods1,2); 
    % Check Photodiode is synchronized with ADC0: Count number of 
    % photodiode pulses in each sweep (of ADC0 FG signal)
    st0 = st_adc0(1:end-1);
    % PD_pulses_sweep = arrayfun(@(s) sum(d1_midpts>=st0(s) & ...
    %     d1_midpts<et_adc0(s)), 1:length(st0));

    p1_tb = table(periods1(:,1),periods1(:,2),diff(periods1,[],2),...
        'VariableNames',{'RisingEdge','FallingEdge','Difference'});
    
    periods1_vector = reshape(periods1.', [], 1);    
    periods1_vector = periods1_vector(~isnan(periods1_vector));
    p1_vector_dif = [periods1_vector,[NaN;diff(periods1_vector)]];

    % Mouse01_RSC_20250717_Shank1to4_RFmapping long PD pulse at 1696 sec
    % p1d = [periods1_vector,[diff(periods1_vector);NaN]];
    % last_pulse = find(p1d(:,2)>0.103,1);
    % periods1_vector = periods1_vector(1:last_pulse); 


    %% Function generator signal sent to Probe data stream %%
    
    if ~use_events
         td385 = (1:length(d385))/sr;
         td385 = td385';
   
        figure
        i385 = td385<=d1_dur;
        plot(td385(i385),d385(i385)), hold on
        ylim([-0.1,1.1])
        title('FG signal, Probe') 
        
        [periods385,in385] = Threshold([td385,double(d385)],'>',0.8,'min',0.2);
        d385_midpts = mean(periods385,2); 
        periods385_1col = reshape(periods385.', [], 1);

        % Testing: 
        y1 = ones(length(periods385),1)*0.9;
        y2 = ones(length(periods385),1)*0.88;
        plot(periods385,y1,'gx','MarkerSize',12)
        plot(periods385,y2,'rx','MarkerSize',12)
    
        all_periods = table(periods0_1col,periods385_1col,periods0_1col-periods385_1col,...
            'VariableNames',{'periods0','periods385','dif'});
    
        all_ts = table(tsd0,tsd385,tsd0-tsd385,'VariableNames',{'tsd0','tsd385','dif'});
    
        all_sn = table(snd0t,snd385t,snd0t-snd385t,'VariableNames',{'snd0t','snd385t','dif'});
        
        blocks1 = 0:sweep_time:d1_dur-sweep_time;
        blocks2 = sweep_time:sweep_time:d1_dur;
        
        % Function generator: 55 cycles per 20 sec sweep
        nperiods = arrayfun(@(n) sum(periods385(:,1)>blocks1(n) & periods385(:,1)<=blocks2(n)),...
            1:ns, 'UniformOutput', false);
        
        p385_tb = table(periods385(:,1),periods385(:,2),periods385(:,2)-periods385(:,1),...
            'VariableNames',{'RisingEdge','FallingEdge','Difference'});
        
        nperiods = cell2mat(nperiods);
        bf = cumsum(nperiods);
        bs = [1,bf(1:end-1)+1];
        
        [~,min_pts] = arrayfun(@(n) min(p385_tb.Difference(bs(n):bf(n))), 1:length(bs));
        min_pts = min_pts+bs-1;
        start_pts = min_pts+1; % Add 1: The next edge should be the largest pulse
        if start_pts(end)>=length(periods385)
            start_pts = start_pts(1:end-1);
        end
        
        % For each start_pt, keep selecting the next pulse if it is larger
        for i = 1:length(start_pts)
            idx = start_pts(i);
            w = p385_tb.Difference(idx);
            while p385_tb.Difference(idx+1)>w || p0_tb.Difference(idx)<0.4 
                start_pts(i) = idx+1;
                idx = idx+1;
                w = p385_tb.Difference(idx);
            end
        end
    
        % Remove duplicate points:
        [~, ~, ic] = unique(start_pts, 'stable');
        [~, first_occurrence] = unique(ic, 'first');
        all_indices = 1:numel(start_pts);
        dup = setdiff(all_indices, first_occurrence);
        start_pts(dup) = [];
        
        st_probe = p385_tb.RisingEdge(start_pts); % Leading pulse rising edge start times
        et_probe = p385_tb.RisingEdge(start_pts(2:end)); % End time: Before next leading pulse
        
        y1 = ones(length(st_probe),1)*0.9;
        y2 = ones(length(et_probe),1)*0.88;
        plot(st_probe,y1,'gx','MarkerSize',12)
        plot(et_probe,y2,'rx','MarkerSize',12)
    
        % Break here & uncomment code to fix edge detection errors:
        % Manually add last edge if missed
        % [x,y] = ginput(1);
        % x = 1601.593;
        % x = 1601.636;
        % st_probe = [st_probe;x];
        % et_probe = [et_probe;x];
        
        np = min(length(st_adc0),length(st_probe));
        st_adc0 = st_adc0(1:np);
        % et_adc0 = et_adc0(1:np);
        st_probe = st_probe(1:np);
        % et_probe = et_probe(1:np);
        
        dif_leading_edges = st_adc0-st_probe;
        dif_incr = [NaN;diff(dif_leading_edges)];
        
        tb1 = table(st_probe,st_adc0,dif_leading_edges,dif_incr,'VariableNames', ...
            {'LeadingEdgeProbe','LeadingEdgeADC0','Dif','Dif_Incr'});
        
        p_sweeptime = [NaN;diff(st_probe)];
        adc_sweeptime = [NaN;diff(st_adc0)];
        tb2 = table(st_probe,p_sweeptime,st_adc0,adc_sweeptime,'VariableNames',...
            {'LeadingEdgeProbe','P_SweepTime','LeadingEdgeADC0','ADC_SweepTime'});
        
        mean_rate = mean(dif_incr,'omitnan');
        rate1s = mean_rate/sweep_time;
    else
        st_probe = snd385t;

        ts_diff = st_adc0-st_probe;
        ts_table = table(st_adc0,st_probe,ts_diff,'VariableNames',...
            {'st_adc0','st_probe','diff'});

        dif = snd0t-snd385t;
        dif_dif = [NaN;diff(dif)];

        sn_table = table(snd0t,snd385t,dif,dif_dif,'VariableNames',...
            {'snd0t','snd385t','dif','dif_dif'});
    end

    if ~sync2adc
        % Align stimulus frames to Probe time:
        % Include linear extrapolation flag for values in periods1_vector >
        % st_adc0. Only use linear extrapolation if there is a linear relationship
        % between st_adc0 and st_probe
        Vq = interp1(st_adc0,st_probe,periods1_vector,'linear','extrap');
        
        % Add the total time of prior recordings
        Vq = Vq+d385_start_time; 
    else
        Vq = periods1_vector;
    end
    
    Vq_periods = [Vq(1:2:end-1),Vq(2:2:end)];
    mean_dif = mean(diff(Vq(~isnan(Vq))));
    nPD_pulses = length(Vq_periods);

    if any(strcmp(protocol,{'Grating','Grating360'}))
        Vq = Vq_periods;
    end

end

function GratingAnalysis_EB(ks_path,fn,trials,GratingTimes,...
    mode_sel,settingsFile,sync2adc,guo)
    % Set paths to data:    
    save_dir = VisStimMatFile('Grating_data');
    EnsureDir(fullfile(save_dir,'PSTH_Data'));
    EnsureDir(fullfile(save_dir,'Summary_Figures'));
    EnsureDir(fullfile(save_dir,'Grating_info'));
    save_data = fullfile(save_dir,'PSTH_Data',fn);
    save_pdf = fullfile(save_dir,'Summary_Figures',fn);
    save_info = fullfile(save_dir,'Grating_info',fn);

    wfp = VisStimMatFile('SpikeWaveforms','mean_wfs',[fn,'.mat']);
    if exist(wfp,'file')==2
        load(wfp)
        plot_wf = true;
    else
        plot_wf = false;
    end

    stimnum = length(trials);
    orinum = 12;
    repnum = stimnum/orinum;
    tf = trials(1).Temporal_Frequency;
    npulses = length(GratingTimes);
    % plot_wf = true;

    % Check if stimulus presentation is complete
    passes_dif = stimnum-npulses;
    if passes_dif == 0
        display('Number of passes = number of pulses')
    elseif passes_dif < 0
        display('Number of pulses > number of passes')
    else
        new_repnum = floor(npulses/orinum);
        sprintf('Number of pulses < number of passes, averaging over %d instead of %d repeats', ...
            new_repnum, repnum)
        repnum = new_repnum;
        stimnum = orinum*repnum;
    end
    
    OrientTiming = cell(orinum,1);
    for i=1:orinum
        OrientTiming{i} = [];
        ori_rows = find(arrayfun(@(r) trials(r).Orientation==30*(i-1), 1:stimnum));
        OrientTiming{i} = [OrientTiming{i}; GratingTimes(ori_rows,1)];
    end   
    
    %% UnitTrig
    stim_dur = mean(diff(GratingTimes,1,2));
    limit = [-1 stim_dur+1];
    % limit = [0 stim_dur];
    win_dur = sum(abs(limit));
    bin_size = 0.035; 
    numbin = floor(win_dur/bin_size);
    dir = 0:30:330;

    if contains(fn,'Mouse')
        sn = regexp(fn,'Mouse\d+_\d{8}_\d+to\d+','match');
        sn = sn{1};
    else 
        sn = fn;
    end
    labels_file = VisStimMatFile('New_Unit_Labels',[sn,'.mat']);
    load(labels_file)

    spike_clusters = readNPY(VisStimInputFile('spike_clusters'));
    uc = unique(spike_clusters);

    if guo % good units only
        good_idx = strcmp(new_labels.KSLabel,'good');
        unit_list = new_labels.cluster_id(good_idx);
    else
        unit_list = uc;
    end
    unit_num = length(unit_list);
    sel = 1:unit_num; 

    if strcmp(mode_sel,'sum')
        sd = [save_data,'_SumOfSpikes.mat'];
        sn = [save_pdf,'_SumOfSpikes'];
    elseif strcmp(mode_sel,'mean')
        sd = [save_data,'.mat'];
        sn = [save_pdf,'_SpikeRate'];
    end

    if exist([save_data,'.mat'], 'file') == 2
        load([save_data,'.mat'],'UnitFeature');   
    else  
        sr = 30000;    
        if sync2adc 
            spike_times = readNPY(VisStimInputFile('adc_spike_times')); % Already in sec
        else
            spike_times = readNPY(VisStimInputFile('spike_times'));
            spike_times = spike_times+1;
            spike_times = double(spike_times)/sr;
        end

        hist_all = zeros(unit_num,orinum,numbin);
        n_phases = 4;
        phase_window = 2*pi/n_phases*1.5;
        CyclePhaseFR = zeros(unit_num, orinum,n_phases);
        
        UnitFeature = [];
        for i=sel
            u = unit_list(i);
            s = spike_times(spike_clusters == u);
    
            for ori=1:orinum
                events = OrientTiming{ori};
    
                [sync,si] = Sync(s,events,'durations', limit);
                if strcmp(mode_sel,'sum')
                    % In this case hist is trial-averaged spike count
                    % during grating presentation
                    hist = length(sync)/repnum; 
                else
                    [hist,t] = SyncHist(sync,si,'durations', limit,'nBins',numbin,'mode',mode_sel);
                end
    
                if ~isempty(hist)
                    hist_all(i,ori,:) = hist;   
                    t_stim = t(t>=0 & t<=stim_dur);
                    hist_stim = hist(t>=0 & t<=stim_dur);
                    baseFR = mean(hist(t<0));
                    
                    total_cyc = tf*round(stim_dur);
                    total_phases = n_phases*tf*(t_stim(end)-t_stim(1));                    
                    cyc_dur = 1/tf;                       
                    phase_dur = cyc_dur/n_phases;         
                    cyc_id = floor(t_stim / cyc_dur) + 1;   
                    phase_id = floor(t_stim / phase_dur) + 1;                    
                    % Wrap into 1–n_phases within each cycle
                    phase_bin_in_cyc = mod(phase_id - 1, n_phases) + 1;

                    inst_phase = mod(2*pi*tf*t_stim, 2*pi);
                    % Define bin centers (e.g., 0, 90°, 180°, 270°)
                    bin_centers = linspace(0, 2*pi, n_phases+1); 
                    bin_centers(end) = [];
                    
                    phase_means = zeros(total_cyc,n_phases);
                    
                    % Phase mean FR using sliding phase bins
                    for cc = 1:total_cyc
                        cyc_mask = (cyc_id == cc);
                        for b = 1:n_phases
                            center = bin_centers(b);
                            % Wrap-around-aware mask for phase window
                            delta_phase = angle(exp(1i*(inst_phase - center)));  % circular diff
                            bin_mask = abs(delta_phase) <= phase_window/2;
                            phase_means(cc, b) = mean(hist_stim(cyc_mask & bin_mask)-baseFR);
                        end
                    end

                    % Phase mean FR using discrete phase bins
                    % for cc = 1:total_cyc
                    %       phase_means(cc,:) = arrayfun(@(p) mean(hist_stim(cyc_id==cc & phase_bin_in_cyc==p)-baseFR),...
                    %           1:n_phases);
                    % end

                    % Fold phase mean FRs over cycles:
                    CyclePhaseFR(i,ori,:) = mean(phase_means,1,'omitnan');
                end
            end
            if i==1
                UnitFeature.('hist_t') = t;
            end
            display(['done ' num2str(i) ' out of ' num2str(unit_num)]);
        end
    
        UnitFeature.('hist') = hist_all;
        UnitFeature.('CyclePhaseFR') = CyclePhaseFR;
        save(sd,'UnitFeature');
    end    
    close all
    
    % Make summary pdf:
    sn = [sn,'_Summary_New.pdf'];
    if exist(sn,'file') == 2
        delete(sn);
    end

    % Save info:
    si = [save_info,'_New.mat'];
    if exist(si,'file') == 2
        delete(si);
    end
    
    hist_t = UnitFeature.hist_t;
    sp_pos = [1:2:orinum-1,2:2:orinum];
    len = 1;
    hist_t_stim = hist_t(hist_t>=0 & hist_t<=stim_dur);
    remap = [7,6,5,4,3,2,1,12,11,10,9,8];

    data.dir = dir;
    data.uid = zeros(unit_num,1);
    data.R_mean = zeros(unit_num,orinum);
    data.R_mean_minus_bl = zeros(unit_num,orinum);
    data.R_pk_minus_bl = zeros(unit_num,orinum);
    data.DSI = zeros(unit_num,1);
    data.pd_rad = zeros(unit_num,1);
    data.pd_deg = zeros(unit_num,1);
    data.OSI = zeros(unit_num,1);
    data.po_rad = zeros(unit_num,1);
    data.po_deg = zeros(unit_num,1);

    for i = sel
        fig = figure('Units','centimeters','Position',[23.336,4.3,21,27]); 
        uid = unit_list(i);       
        sgtitle(['Unit ' num2str(uid)])

        for j = 1:orinum 
            ca = subplot((orinum/2)+1,2,sp_pos(j)); 
            ca_pos = ca.Position;
            h = squeeze(UnitFeature.hist(i,j,:));
            bar(hist_t, h,'FaceColor',[.2,.2,.2],'EdgeColor',[.2,.2,.2]), hold on
            set(ca,'box','off','TickDir','out')
            ca.YLim(1) = -1;
            bl = mean(h(hist_t<0));
            sd = std(h(hist_t<0.5));
            bl2sd = bl+(2*sd);
            if ca.YLim(2) < bl2sd
                ca.YLim(2) = ca.YLim(2)+10;
            end
            plot(ca.XLim(1)+0.0001,bl,'bx','MarkerSize',5,'LineWidth',1.4)
            plot(ca.XLim(1)+0.0001,bl2sd,'x','Color',rgb('purple'),'MarkerSize',...
                5,'LineWidth',1.4)
            if j == 1
                ylabel('imp/s');
            elseif  j == orinum/2 
                xlabel('time (s)');
            end
            ax = axes('Position', [ca_pos(1)+0.005,ca_pos(2)+0.07,0.022,0.015]);
            x0 = 0; y0 = 0;
            angle_deg = dir(remap(j));
            angle_rad = deg2rad(angle_deg);
            dx = len * cos(angle_rad);
            dy = len * sin(angle_rad);
            quiver(x0, y0, dx, dy, 0, 'LineWidth', 1, 'MaxHeadSize', 1.5)
            axis equal, box off, axis off

            % Plot Phase response:
            ph_curve = squeeze(UnitFeature.CyclePhaseFR(i,j,:));
            ca_pos = ca.Position;
            ph_ax = axes('Position', ca_pos.*[1,1,.18,.28]);
            ph_ax_pos = ph_ax.Position;
            ph_ax.Position(1:2) = [ca_pos(1)+ca_pos(3)-ph_ax_pos(3),...
                ca_pos(2)+(1.1*ca_pos(4))-ph_ax_pos(4)];
            px = [0,90,180,270];
            plot(px,ph_curve,'-o','Color',[.2,.2,.2],'MarkerSize',4)
            if j == 1
                hold on,
                set(ph_ax,'XTick',px,'XTickLabel',{0,90,180,270},'box','off',...
                    'FontSize',7)
            else
                 set(ph_ax,'XTick',px,'XTickLabel',{},'box','off')
            end           
        end
        axs = findall(gcf, 'Type', 'axes');
        plots = axs(3:3:end);
        mxy = max(arrayfun(@(p) max(plots(p).YLim(2)), 1:orinum));
        arrayfun(@(p) set(p,'YLim',[-1,mxy]), plots)
        arrows = axs(2:3:end);  

        % Plot mean spike waveform
        if plot_wf
            psth_ax = axs(3:3:end);
            hist7_ax = psth_ax(6);
            hist7_pos = hist7_ax.Position;
            wf_ax = axes('Position',hist7_pos.*[1,1.13,0.25,0.4]);
            u_idx = find(meanWaveforms.unitIds==uid);
            ch_idx = meanWaveforms.ptp_chan_idx(u_idx);
            wf = meanWaveforms.data(ch_idx,:,u_idx);
            plot(meanWaveforms.timepts,wf,'Color',[.2,.2,.2]), hold on % cla
            yl1 = 1.3*wf_ax.YLim(1);
            plot([0,1],[yl1,yl1],'k-')
            text(0.25,1.4*yl1,'1 ms','FontSize',8)
            axis off
        end
                  
        % Adjust phase plots
        ph_ax = axs(1:3:end);
        ph_curves = squeeze(UnitFeature.CyclePhaseFR(i,:,:));
        ph_mxy = max(ph_curves(:));
        yl1 = min(0,floor(min(ph_curves(:))));
        yl1 = yl1-(2-mod(yl1,2));
        yl2 = ceil(ph_mxy);
        yl2 = yl2+(2-mod(yl2,2));
        A = diff([yl1,yl2])*0.5;  
        yc = mean([yl2,yl1]);
        xp = linspace(0,270,100);
        cos_wave = A * cos(deg2rad(xp))+yc;  
        set(ph_ax,'YLim',[yl1, yl2]);
        yt = ph_ax(1).YTick([1,end]); ytl = ph_ax(1).YTickLabel([1,end]);
        set(ph_ax,'YTick',yt,'YTickLabel',ytl,'Color', [0.98 0.98 0.98]);
        set(ph_ax(1:end-1),'YTickLabel',{});  
        plot(ph_ax(end), xp, cos_wave, 'Color', rgb('lightblue'))
      
        A = mxy*0.1;
        stim_wave = A*cos((2*pi*tf)*hist_t_stim)+A;
        arrayfun(@(p) plot(plots(p), hist_t_stim, stim_wave, 'Color',...
           rgb('gray')), 1:orinum);
        plots_inorder = flipud(plots);
        arrayfun(@(n) text(plots_inorder(n), ca.XLim(1)+0.1, mxy, ...
            sprintf('%d°', dir(n)),'FontSize',8), 1:orinum);

        % Data for polar plots:
        h = squeeze(UnitFeature.hist(i,1:orinum,:));
        bl = mean(h(:,hist_t<0),2);
        h_mean = mean(h(:,hist_t>=0 & hist_t<stim_dur),2);
        h_mean_minus_bl = h_mean-bl;
        h_pk = max(h(:,hist_t>=0 & hist_t<stim_dur),[],2);
        h_pk_minus_bl = h_pk-bl;
        R = h_mean_minus_bl;
        yd = [h_mean,h_mean_minus_bl,h_pk_minus_bl];
        yd = [yd;yd(1,:)];
        yd(yd<0) = 0; % Zero negative values
        theta_rad = deg2rad([dir,dir(1)]);

        R(R<0) = 0; % Zero negative responses
        % Calculate vector DSI       
        tr = theta_rad(1:orinum)';
        DSI_complex = sum(R .* exp(1i * tr(1:orinum))) / sum(R);
        DSI = abs(DSI_complex);  % selectivity index (modulus)
        pd_rad = angle(DSI_complex);
        pd_deg = round(mod(rad2deg(pd_rad), 360)); % preferred direction (deg)

        % Calculate vector OSI
        R_orient = zeros(1, length(R)/2); % Average responses to opposite directions
        theta_orient = zeros(1, length(R)/2);
        for k = 1:orinum/2
            opp = mod(k-1 + length(R)/2, length(R)) + 1;
            R_orient(k) = (R(k) + R(opp)) / 2;
            theta_orient(k) = theta_rad(k); % orientation angle
        end
        
        % OSI calculation using vector sum with 2*theta
        vec = sum(R_orient.*exp(1i*2*theta_orient));
        OSI = abs(vec) / sum(R_orient);
        po_rad = angle(vec)/2;
        po_deg = mod(rad2deg(po_rad), 180);

        data.uid(i) = uid;
        data.R_mean(i,:) = h_mean;
        data.R_mean_minus_bl(i,:) = R;
        data.R_pk_minus_bl(i,:) = h_pk_minus_bl;
        data.DSI(i) = DSI;
        data.pd_rad(i) = pd_rad;
        data.pd_deg(i) = pd_deg;
        data.OSI(i) = OSI;
        data.po_rad(i) = po_rad;
        data.po_deg(i) = po_deg;
        
        subplot((orinum/2)+1,2,orinum+1:orinum+2);
        nPlots = 3;
        polarplot(theta_rad, yd(:,1)) 
        p = gca;
        base_pos = get(p, 'Position');  
        delete(p);         
        total_width = 0.9;                      
        plot_width = total_width/nPlots;     
        plot_height = base_pos(4);             
        bottom = base_pos(2)-0.07;                   
        p = gobjects(1,nPlots);
        pl = {'Mean FR','Mean FR-baseline','Peak FR-baseline'};
        sub = [0,0.08,0.16];
        for k = 1:nPlots   
            left = (k*plot_width)-sub(k);
            pos = [left, bottom, plot_width, plot_height];
            ca = axes('Position',pos); % cla
            polarplot(theta_rad, yd(:,k)), hold on
            p(k) = gca;           
            set(p(k), 'ThetaZeroLocation','left','ThetaDir','clockwise')
            annotation('textbox', [left, bottom+plot_height+0.025, plot_width, 0.05],...
                'String', pl{k}, 'EdgeColor', 'none', 'HorizontalAlignment', 'center','FontSize',9);
            if k == 2  
                polarplot([0,pd_rad],[0,DSI*max(R)],'Color',[0,.8,0],'LineWidth',1.2) 
                if (DSI<0.3 && OSI<0.3) || max(R)<5
                    str = sprintf('\n\n\nDSI: %.2f, PD: ~\nOSI: %.2f, PO: ~',...
                        DSI,OSI);
                elseif DSI<0.3
                    str = sprintf('\n\n\nDSI: %.2f, PD: ~\nOSI: %.2f, PO: %d°',DSI,...
                       OSI,round(po_deg));
                elseif OSI<0.3
                    str = sprintf('\n\n\nDSI: %.2f, PD: %d°\nOSI: %.2f, PO: ~',DSI,...
                       round(pd_deg),OSI);
                else
                    str = sprintf('\n\n\nDSI: %.2f, PD: %d°\nOSI: %.2f, PO: %d°',DSI,...
                        round(pd_deg),OSI,round(po_deg));
                end

                annotation('textbox', [left-0.04, bottom+plot_height+0.04, plot_width, 0.05],...
                'String',str, 'EdgeColor', 'none', 'HorizontalAlignment', 'left','FontSize',9);
                        
                compass_ax = axes('Position',[left+(plot_width*.8),bottom+plot_height,...
                plot_width/9,plot_width/9]); 
                plot([.5,.5],[0,1],'k-'), hold on, plot([0,1],[.5,.5],'k-')
                text(0.35,1.25,'D','FontSize',9)
                text(0.35,-0.25,'V','FontSize',9)
                text(-0.28,0.5,'T','FontSize',9)
                text(1.1,0.5,'N','FontSize',9), axis square, axis off
            end
        end           
        arrayfun(@(ax) set(ax, 'RTickLabel', [ax.RTickLabel(1), repmat({''},...
            1, numel(ax.RTickLabel)-2), ax.RTickLabel(end)]), p(1:nPlots));
        delete_labels = ~ismember(p(1).ThetaTickLabel,{'0°','90°','180°','270°'});
        theta_labels = p(1).ThetaTickLabel;
        theta_labels(delete_labels) = {'','','','','','','',''};        
        arrayfun(@(n) set(p(n),'ThetaTickLabel',theta_labels), 1:length(p))  

        probe_pos = [0.05, bottom, plot_width, plot_height*1.5];
        if plot_wf
            PlotProbeConfig('Grating',ch_idx,settingsFile,probe_pos)
        end
     
        exportgraphics(fig,sn,'Append',true);
        close(fig);
    end
    save(si,'data')
end

function RFmapping_EB(ks_path,fn,trials,Vq,mode_sel,nbins,save_figs,...
    save_gauss,gauss_2d_data,gdi,settingsFile,sync2adc,guo)
  
    close all
    save_dir = VisStimMatFile('RF_maps');
    EnsureDir(fullfile(save_dir,'Spike_Data'));
    EnsureDir(fullfile(save_dir,'Summary_Figures'));
    EnsureDir(fullfile(save_dir,'Gaussian_Data'));
    EnsureDir(fullfile(save_dir,'Pattern_CSV'));
    save_data = fullfile(save_dir,'Spike_Data',fn);
    save_pdf = fullfile(save_dir,'Summary_Figures',fn);
    save_gd = fullfile(save_dir,'Gaussian_Data',fn);
    save_csv = fullfile(save_dir,'Pattern_CSV',[fn,'_RFmap_',mode_sel,'.csv']);
    load(VisStimMatFile('SpikeWaveforms','mean_wfs',[fn,'.mat']))

    txt = fileread(settingsFile);
    channels_line = regexp(txt, '<CHANNELS[^>]*>', 'match', 'once');
    tokens = regexp(channels_line, '(CH\d+)="([^"]*)"', 'tokens');
    channel_names  = cellfun(@(x) x{1}, tokens, 'UniformOutput', false);
    channel_map = str2double(strrep(channel_names,'CH',''));

    npulses = length(Vq);
    last_offset = Vq(end)+mean(diff(Vq));
    Vq_periods = [Vq,[Vq(2:end);last_offset]];
    
    nframes = height(trials);
    VisStim.periods = Vq_periods;
    VisStim.duration = VisStim.periods(:,2) - VisStim.periods(:,1);
    VisStim.PosX = [trials(1:nframes).Square_PositionX]';
    VisStim.PosY = [trials(1:nframes).Square_PositionY]';
    VisStim.Lum = [trials(1:nframes).Square_Luminance]';
    VisStim.SquareSize = trials(1).Square_Size;        
    
    sr = 30000;
    sr_adc = 30300.5;

    if sync2adc 
        spike_times = readNPY(VisStimInputFile('adc_spike_times')); % Already in sec
    else
        spike_times = readNPY(VisStimInputFile('spike_times'));
        spike_times = spike_times+1;
        spike_times = double(spike_times)/sr;
    end     
    
    spike_clusters = readNPY(VisStimInputFile('spike_clusters'));
    uc = unique(spike_clusters);
    unit_num = length(uc); 

    if contains(fn,'Mouse')
        sn = regexp(fn,'Mouse\d+_\d{8}_\d+to\d+','match');
        sn = sn{1};
    else
        sn = fn;
    end
       
    labels_file = VisStimMatFile('New_Unit_Labels',[sn,'.mat']);
    load(labels_file)

    if guo % good units only
        good_idx = strcmp(new_labels.KSLabel,'good');
        unit_list = new_labels.cluster_id(good_idx); % 0-indexing, corresponding to Phy
    else
        unit_list = uc;
    end
    unit_num = length(unit_list);

    sel = 1:unit_num; % Select units for testing code
 
    SqDeg = VisStim.SquareSize;
    RFmap = cell(unit_num,1);    
    x_num = length(unique(VisStim.PosX));
    y_num = length(unique(VisStim.PosY));
    u_xy = length(unique([VisStim.PosX,VisStim.PosY,VisStim.Lum],'rows'));
    xy_ratio = x_num/y_num;

    % white squares and black squares each have [repnum] reps
    repnum = nframes/u_xy;  
   
    % Check if stimulus presentation is complete
    passes_dif = nframes-npulses;
    if passes_dif == 0
        display('Number of passes = number of pulses')
    elseif passes_dif < 0
        display('Number of pulses > number of passes')
    else
        new_repnum = floor(npulses/u_xy);
        sprintf('Number of pulses < number of frames, averaging over %d instead of %d repeats', ...
            new_repnum, repnum)
        repnum = new_repnum;
        nframes = u_xy*repnum;

        VisStim.periods = VisStim.periods(1:nframes,:);
        VisStim.duration = VisStim.duration(1:nframes);
        VisStim.PosX = VisStim.PosX(1:nframes);
        VisStim.PosY = VisStim.PosY(1:nframes);
        VisStim.Lum = VisStim.Lum(1:nframes);
    end
    ss = VisStim.SquareSize;

    if strcmp(mode_sel,'sum')
        sd = [save_data,'_RFmap_SumOfSpikes.mat'];
        sn = [save_pdf,'_Maps_SumOfSpikes.pdf'];
    elseif strcmp(mode_sel,'mean')
        sd = [save_data,'_RFmap_SpikeRate.mat'];
        sn = [save_pdf,'_Maps_SpikeRate.pdf'];
    end

    if exist(sd) == 2
        load(sd);
    else
        for k=1:unit_num
            RFmap{k}.ON.OnSet = zeros(y_num,x_num,nbins);
            RFmap{k}.OFF.OnSet = zeros(y_num,x_num,nbins);
        end

        for k=sel
            u = unit_list(k);
            s = spike_times(spike_clusters == u);
            % Get mean spike sum or rate
            [sync,i] = Sync(s,Vq_periods(1,1),'durations', [-5 0]);
            [baseline,~] = SyncHist(sync,i,'durations', [-5 0],'nBins',1,'mode',mode_sel);
            if isempty(baseline)
                baseline = 0;
            end
            RFmap{k}.baseline = baseline;

            for x=1:x_num
                for y=1:y_num
                    curX = -(x_num-1)/2*SqDeg + (x-1)*SqDeg;
                    curY = -(y_num-1)/2*SqDeg + (y-1)*SqDeg;
                    IDon = VisStim.PosX==curX&VisStim.PosY==curY&VisStim.Lum==1;
                    IDoff= VisStim.PosX==curX&VisStim.PosY==curY&VisStim.Lum==0;
        
                    [sync,i] = Sync(s,VisStim.periods(IDon,1),'durations', [0 0.1]);
                    [hist,~] = SyncHist(sync,i,'durations', [0 0.1],'nBins',nbins,'mode',mode_sel);
                    if ~isempty(hist)
                        RFmap{k}.ON.OnSet(y,x,:) = hist;
                    end
        
                    [sync,i] = Sync(s,VisStim.periods(IDoff,1),'durations', [0 0.1]);
                    [hist,~] = SyncHist(sync,i,'durations', [0 0.1],'nBins',nbins,'mode',mode_sel);
                    if ~isempty(hist)
                        RFmap{k}.OFF.OnSet(y,x,:) = hist;
                    end
                end
            end 
            display(['done ' num2str(k) ' out of ' num2str(unit_num)]);
        end
        save(sd,'RFmap');
    end  
    xdeg = unique(VisStim.PosX);
    ydeg = unique(VisStim.PosY);
    total_deg = max(xdeg) - min(xdeg) + ss;
    SaveRfPatternCsv(RFmap, unit_list, save_csv, mode_sel, total_deg);

    % Make summary pdf
    if isempty(gauss_2d_data)

        if save_figs && exist(sn) == 2 
            delete(sn)
        end
    
        gauss_2d_data = cell(length(sel),1);
        for k=sel
            u = unit_list(k); 

            u_idx = find(meanWaveforms.unitIds==u);
            ch_idx = meanWaveforms.ptp_chan_idx(u_idx);
            wf = meanWaveforms.data(ch_idx,:,u_idx);
    
            % Get largest amplitude waveform and waveforms on neighbouring channels
            chans_idx = ch_idx-2:ch_idx+2;
            chans_idx = chans_idx(chans_idx > 0 & chans_idx < 385);    
            nwfs = meanWaveforms.data(chans_idx,:,u_idx);
    
            fig=figure('Units','centimeters','Position',[23.336,4.3,17,17]);
            m = {RFmap{k}.ON.OnSet,RFmap{k}.OFF.OnSet};
            t = {'ON stim RF map','OFF stim RF map'};
            gauss_2d = cell(1,2);
            sb_pix = 30/ss; % Make scale bar 30°
            baseline = RFmap{k}.baseline;
            for ii = 1:2
                sp = subplot(2,2,ii);
                if strcmp(mode_sel,'sum')
                    rf = sum(m{ii},3)-baseline;
                elseif strcmp(mode_sel,'mean')
                    rf = mean(m{ii},3)-baseline;
                    mx_fr = max(rf(:));
                    min_fr = min(rf(:));                
                    if mx_fr>0 && mx_fr>abs(min_fr)
                        kc_sign = 1;
                    else
                        kc_sign = -1;
                    end
                end
                imagesc(xdeg,ydeg,rf); hold on 
                pos = sp.Position;           
                sp.Position(4) = pos(3)/xy_ratio;  
                cb = colorbar;
                cb_pos = cb.Position;
                cb.Position([1,3]) = [1.02*(pos(1)+pos(3)),cb_pos(3)*0.6]; 
                im = sp.Children; 
                axis off % axis on
                plot([sp.XLim(2),sp.XLim(2)-30],[sp.YLim(2),sp.YLim(2)],'r-',...
                    'LineWidth',1.2)
                title(t{ii});
                if ii == 1
                    text(sp.XLim(2)-15,sp.YLim(2)+5,'30°')
                elseif strcmp(mode_sel,'sum')
                    cb.Label.String = '# spikes';
                elseif strcmp(mode_sel,'mean')
                    cb.Label.String = 'imp/s';
                end
                % Compute 2-D gaussian fit
                % TO DO: 
                % 1. Adjust p0, LB, UB to better capture RFs on the edges
                % 2. Include inhibitory RFs (k: 96,107,126)
                % gauss_2d_model = @(p,x,y) ...
                %     p(1) * exp(-((( ( (x - p(2))*cos(p(7)) + (y - p(4))*sin(p(7)) ).^2 ) / (2*p(3)^2) + ...
                %       ((-(x - p(2))*sin(p(7)) + (y - p(4))*cos(p(7)) ).^2 ) / (2*p(5)^2)))) + p(6);
                gauss_2d_model = @(p,x,y) ...
                    p(1) * exp(-((( ( (x - p(2))*cos(p(6)) + (y - p(4))*sin(p(6)) ).^2 ) / (2*p(3)^2) + ...
                      ((-(x - p(2))*sin(p(6)) + (y - p(4))*cos(p(6)) ).^2 ) / (2*(p(3)*p(7))^2)))) + p(5);
    
                [x,y] = meshgrid(xdeg, ydeg);
                xy = [x(:),y(:)];
                gauss_fun = @(p) sum((gauss_2d_model(p, x(:), y(:)) - rf(:)).^2);
    
                % p(1): Amplitude
                % p(2): Center x-coordinate
                % p(3): Standard deviation along x (σₓ)
                % p(4): Center y-coordinate
                % p(5): Baseline spike sum/rate (free parameter, baseline already subtracted)
                % p(6): Gaussian orientation in radians
                % p(7): σᵧ/σₓ ratio, constrained to keep minor axis length at least 0.5 x major axis length
                % p(8): Standard deviation along y (σᵧ) determined during fit, added to pfit_cell
                kc = max(rf(:));
                cx = 0;
                sdx = 3*SqDeg;
                cy = 0;
                b = 0;
                theta = 0;
                r = 1;
                
                % Note making LB of kc negative reduces the accuracy of many
                % fits. Find a way to decide for individual cells whether to
                % use a negative kc LB.
                p0 = [kc, cx, sdx, cy, b, theta, r];
                LB = [0, min(xdeg)-2*SqDeg, SqDeg, min(ydeg)-2*SqDeg, 0, -pi/2, 0.6];
                UB = [3*kc, max(xdeg)+2*SqDeg, 0.5*max(ydeg), max(ydeg)+2*SqDeg, 0.25*kc, pi/2, 1.9];
    
                % Adjust parameters for inhibitory RF
                if kc_sign==-1
                    kc = min(rf(:));
                    p0(1) = kc;
                    LB([1,5]) = [3*kc,0.25*kc];
                    UB([1,5]) = [0,0];
                end
    
                % inequality constraint c(p) <= 0 → ensures min >= 0.5*max
                nonlcon = @(p) deal([], 0.5*max(p(3),p(5)) - min(p(3),p(5))); 
                % pfit = fmincon(gauss_fun, p0, [], [], [], [], LB, UB, nonlcon);           
                pfit = lsqcurvefit(@(p,xy) gauss_2d_model(p, xy(:,1), xy(:,2)), ...
                    p0, [x(:), y(:)], rf(:), LB, UB); 
    
                sdy = pfit(7)*pfit(3); % σᵧ
                txt = sprintf('k: %0.1f imp/s, σx: %0.1f°, σy: %0.1f°\nΘ: %0.1f°, sign: %d',...
                    pfit([1,3]),sdy,rad2deg(pfit(6)),kc_sign);
    
                gauss_2d{ii}.pfit = [pfit,sdy];
                gauss_2d{ii}.pfit_labels = {'k(imp/s)','cx(°)','σx(°)','cy(°)','baseline(imp/s)', ...
                  'theta(rad)','σy/σx','σy(°)'};
                gauss_2d{ii}.rf_fit = gauss_2d_model(pfit, x, y);           
                text(sp.XLim(1),1.5*sp.YLim(2),txt)
            end 
            colormap(gray) 
            ax = findall(fig,'Type','axes');
            cmx = max(arrayfun(@(n) ax(n).CLim(2), 1:length(ax)));
            cmin = min(arrayfun(@(n) ax(n).CLim(1), 1:length(ax)));
            arrayfun(@(n) set(ax(n),'CLim',[cmin,cmx]), 1:length(ax));
    
            ax_inorder = flipud(ax);
            t = linspace(0, 2*pi, 300);
            for jj = 1:2
                axes(ax_inorder(jj))
                pfit = gauss_2d{jj}.pfit;                    
                % Unrotated ellipse (axis-aligned)
                xe = pfit(3) * cos(t);
                ye = pfit(8) * sin(t);        
                % Rotate by theta
                theta = pfit(6);
                R = [cos(theta) -sin(theta); sin(theta) cos(theta)];
                xy_rot = R * [xe; ye];
                % Translate to Gaussian center
                xe_rot = xy_rot(1,:) + pfit(2);
                ye_rot = xy_rot(2,:) + pfit(4);
                plot(xe_rot, ye_rot, 'b', 'LineWidth', 1);
            end      
            sgtitle(sprintf('Unit %d',u))
    
            % Plot mean spike waveform
            yshift = 1000;
            ax1_pos = ax(1).Position;
            % wf_ax = axes('Position',ax1_pos.*[1,0.6,0.4,0.5]); 
            wf_ax = subplot(2,2,4);
            wf_ax_pos = wf_ax.Position;           
            for ww = 1:length(chans_idx)
                if chans_idx(ww) == ch_idx
                    plot(meanWaveforms.timepts,nwfs(ww,:)+((ww-1)*yshift),'Color',rgb('DodgerBlue'))
                else
                    plot(meanWaveforms.timepts,nwfs(ww,:)+((ww-1)*yshift),'Color',[.2,.2,.2]) 
                end
                hold on % cla
            end
            yl1 = wf_ax.YLim(1)-1;
            plot([0,0.001],[yl1,yl1],'k-')
            text(0.00025,yl1-2,'1 ms','FontSize',8), axis off
            wf_ax.Position = [wf_ax_pos(1:2).*[1.05,1.5], ax1_pos(3:4).*[0.4,1]];
            ty = 0:yshift:yshift*length(chans_idx);
            arrayfun(@(w) text(1.1*wf_ax.XLim(2),ty(w),num2str(channel_map(chans_idx(w)))),...
                1:length(chans_idx))
                        
            PlotProbeConfig('RFmapping',ch_idx,settingsFile)
            gauss_2d_data{find(sel==k)} = gauss_2d;
    
            if save_figs
                exportgraphics(fig, sn, 'Append', true);        
                close(fig);
            end
        end
        if save_gauss
            save(save_gd,'gauss_2d_data')
        end
    else
        load(gdi)
    end
    
    fig=figure('Units','centimeters','Position',[23.336,4.3,17,17]);
    colors = distinguishable_colors(length(sel)); 
    handles = gobjects(length(gauss_2d_data),1);   
    labels  = zeros(length(gauss_2d_data),1);  
    titles = {'ON stimulus','OFF stimulus'};
    for ss = 1:2
        sp = subplot(2,2,ss);
        set(sp,'XLim',xdeg([1,end]),'YLim',ydeg([1,end]))
        pos = sp.Position;           
        sp.Position(4) = pos(3)/xy_ratio;  
    
        for kk = 1:length(gauss_2d_data) 
            pfit = gauss_2d_data{kk}{ss}.pfit;
            t = linspace(0, 2*pi, 300);        
            % Unrotated ellipse (axis-aligned)
            xe = pfit(3) * cos(t);
            ye = pfit(8) * sin(t);        
            % Rotate by theta
            theta = pfit(6);
            R = [cos(theta) -sin(theta); sin(theta) cos(theta)];
            xy_rot = R * [xe; ye];
            % Translate to Gaussian center
            xe_rot = xy_rot(1,:) + pfit(2);
            ye_rot = xy_rot(2,:) + pfit(4);
            % Invert ye: negative y values correspond to top of stim monitor  
            h = plot(xe_rot, -ye_rot, 'Color', colors(kk,:), 'LineWidth', 1); hold on
            handles(kk) = h;
            labels(kk) = kk;
        end
        set(sp,'XLim',xdeg([1,end]),'YLim',ydeg([1,end]))
        title(titles(ss))
    end
    valid = labels ~= 0;
    legend(handles(valid), string(labels(valid)),'Location','southoutside', ...
           'NumColumns', 4);
    ax = findall(gcf, 'type','axes');
    xlabel(ax(2),'deg')
    ylabel(ax(2),'deg')

    if save_figs
        exportgraphics(fig, sn, 'Append', true);        
        close(fig);
    end
end

function SaveRfPatternCsv(RFmap, unit_list, csvFile, mode_sel, total_deg)
    EnsureDir(fileparts(csvFile));

    innerBlankRows = 4;
    polarPadRows = 1;
    unitNum = length(unit_list);
    sampleRf = CollapseRfMatrix(RFmap{1}.ON.OnSet, RFmap{1}.baseline, mode_sel);
    [rfRows, rfCols] = size(sampleRf);
    stimLabels = {'ON','OFF'};
    rowCount = unitNum * length(stimLabels) * rfRows * rfCols;

    stimulus = cell(rowCount, 1);
    unit_index = zeros(rowCount, 1);
    unit_id = zeros(rowCount, 1);
    row = zeros(rowCount, 1);
    col = zeros(rowCount, 1);
    rf_value = zeros(rowCount, 1);
    theta_start_deg = zeros(rowCount, 1);
    theta_end_deg = zeros(rowCount, 1);
    r_inner = zeros(rowCount, 1);
    r_outer = zeros(rowCount, 1);
    rf_rows = rfRows * ones(rowCount, 1);
    rf_cols = rfCols * ones(rowCount, 1);
    total_deg_col = total_deg * ones(rowCount, 1);
    inner_blank_rows = innerBlankRows * ones(rowCount, 1);
    polar_plot_radius = (innerBlankRows + rfRows + polarPadRows) * ones(rowCount, 1);

    thetaEdges = linspace(90 + total_deg / 2, 90 - total_deg / 2, rfCols + 1);
    rEdges = innerBlankRows:(innerBlankRows + rfRows);

    idx = 1;
    for k = 1:unitNum
        baseline = RFmap{k}.baseline;
        matrices = {CollapseRfMatrix(RFmap{k}.ON.OnSet, baseline, mode_sel), ...
            CollapseRfMatrix(RFmap{k}.OFF.OnSet, baseline, mode_sel)};

        for stimIdx = 1:length(stimLabels)
            rf = flipud(matrices{stimIdx});
            for rr = 1:rfRows
                for cc = 1:rfCols
                    stimulus{idx} = stimLabels{stimIdx};
                    unit_index(idx) = k;
                    unit_id(idx) = unit_list(k);
                    row(idx) = rr;
                    col(idx) = cc;
                    rf_value(idx) = rf(rr, cc);
                    theta_start_deg(idx) = thetaEdges(cc);
                    theta_end_deg(idx) = thetaEdges(cc + 1);
                    r_inner(idx) = rEdges(rr);
                    r_outer(idx) = rEdges(rr + 1);
                    idx = idx + 1;
                end
            end
        end
    end

    T = table(stimulus, unit_index, unit_id, row, col, rf_value, ...
        theta_start_deg, theta_end_deg, r_inner, r_outer, rf_rows, ...
        rf_cols, total_deg_col, inner_blank_rows, polar_plot_radius, ...
        'VariableNames', {'stimulus','unit_index','unit_id','row','col', ...
        'rf_value','theta_start_deg','theta_end_deg','r_inner','r_outer', ...
        'rf_rows','rf_cols','total_deg','inner_blank_rows','polar_plot_radius'});

    if exist(csvFile, 'file') == 2
        delete(csvFile);
    end
    writetable(T, csvFile);
end

function rf = CollapseRfMatrix(rf_data, baseline, mode_sel)
    if strcmp(mode_sel,'sum')
        rf = sum(rf_data, 3) - baseline;
    elseif strcmp(mode_sel,'mean')
        rf = mean(rf_data, 3) - baseline;
    end
end

function PlotProbeConfig(protocol,chid_pos,settingsFile,varargin)

switch protocol
    case 'RFmapping'
        probe_plot = subplot(2,2,3); hold on
        probe_pos = probe_plot.Position;
        probe_plot.Position(3:4) = probe_pos(3:4).* [0.8,0.6];
    case 'Grating'
        probe_pos = varargin{1};
        probe_plot = axes('Position',probe_pos); hold on
end

% Probe parameters 
shank_width = 70;      
shank_height = 8000;  
tip_height = 600;

% Load XML probe layout
txt = fileread(settingsFile);
channels_line = regexp(txt, '<CHANNELS[^>]*>', 'match', 'once');
tokens = regexp(channels_line, '(CH\d+)="([^"]*)"', 'tokens');
channel_names  = cellfun(@(x) x{1}, tokens, 'UniformOutput', false);
chids = str2double(strrep(channel_names,'CH',''));

xp_line = regexp(txt, '<ELECTRODE_XPOS[^>]*>', 'match', 'once');
tokens = regexp(xp_line, '(CH\d+)="(\d+)"', 'tokens');
xpos = str2double(cellfun(@(x) x{2}, tokens, 'UniformOutput', false));

yp_line = regexp(txt, '<ELECTRODE_YPOS[^>]*>', 'match', 'once');
tokens = regexp(yp_line, '(CH\d+)="(\d+)"', 'tokens');
ypos = str2double(cellfun(@(x) x{2}, tokens, 'UniformOutput', false));

layout = table(chids',xpos',ypos','VariableNames',{'chid','xpos','ypos'});

ux = unique(xpos);
dx = diff(ux);
gap_threshold = 100;   % large enough to separate shanks
split_idx = [0; find(dx > gap_threshold)'; numel(ux)];
num_shanks = numel(split_idx)-1;

for s = 1:num_shanks
    idx_start = split_idx(s)+1;
    idx_end = split_idx(s+1);
    shank_x = ux(idx_start:idx_end);
    cx = mean(shank_x);

    fill([cx-shank_width/2 cx+shank_width/2 cx+shank_width/2 cx-shank_width/2], ...
         [0 0 shank_height shank_height], ...
         [0.8 0.8 0.8],'EdgeColor',[0.8 0.8 0.8]);

    fill([cx-shank_width/2 cx+shank_width/2 cx], ...
         [0 0 -tip_height], ...
         [0.8 0.8 0.8],'EdgeColor',[0.8 0.8 0.8]);
end
xlim([-50,900]) % cla

% Plot channel config and highlight current channel 
plot(xpos, ypos,'.', 'Color',[0.3 0.3 0.3], 'MarkerSize',0.8); % cla
plot(xpos(chid_pos), ypos(chid_pos), '.', 'Color', rgb('dodgerblue'), 'MarkerSize', 10);

ca = gca;
pos = ca.Position;
ca.Position([1,3]) = pos([1,3]).*[2,.7];
box off
axis off

end

function colors = distinguishable_colors(n)
% Generate n maximally distinct, vivid colors (no white, no gray)

    % Sample RGB cube
    M = 40;
    [r,g,b] = ndgrid(linspace(0,1,M));
    cand = [r(:), g(:), b(:)];

    % Compute brightness and saturation
    brightness = max(cand,[],2);
    sat = std(cand,0,2);   % saturation ~ channel variance (0 = gray/white)

    % Remove near-white OR near-gray/faint colors
    keep = (brightness < 0.9) & (sat > 0.05);

    cand = cand(keep,:);

    % Safety: must have candidates
    if size(cand,1) < n
        error('Not enough vivid colors; reduce n or loosen thresholds.');
    end

    % Greedy farthest-point sampling
    colors = zeros(n,3);
    colors(1,:) = cand(randi(size(cand,1)),:);  % random vivid seed

    for i = 2:n
        d = sqrt(sum((reshape(cand,[],1,3) - reshape(colors(1:i-1,:),1,[],3)).^2,3));
        score = min(d,[],2);
        [~,best] = max(score);
        colors(i,:) = cand(best,:);
    end
end

function SavePDF(file_name)

figs = findall(0, 'Type', 'figure');
[~, idx] = sort([figs.Number]);
figs = figs(idx);
if exist(file_name,'file')==2
    delete(file_name)
end
for i = 1:numel(figs)
    if i == 1
        exportgraphics(figs(i), file_name);
    else
        exportgraphics(figs(i), file_name, 'Append', true);
    end
end

end

function filledMask = fill_short_gaps(logicalMask, maxGapSamples)
%FILL_SHORT_GAPS Fill short false gaps between true samples in a logical mask.
%
% filledMask = fill_short_gaps(logicalMask, maxGapSamples)
%
% Inputs
%   logicalMask   : logical (or numeric) vector/array; treated as logical
%   maxGapSamples : maximum gap length (in samples) to bridge (default = 600)
%
% Output
%   filledMask    : logical mask with short gaps filled

    % Linear indices where the mask is true
    filledMask = logicalMask;
    trueSampleIndices = find(filledMask);
    % Nothing to bridge if fewer than two true samples
    if numel(trueSampleIndices) < 2
        return;
    end
    % Gap length between consecutive true samples (minus 1)
    gapLengths = diff(trueSampleIndices) - 1;
    % Locations (in the diff array) where the gap is short enough to fill
    shortGapLocations = find((gapLengths > 0) & (gapLengths <= maxGapSamples));
    % Fill each short gap
    for locationIdx = 1:numel(shortGapLocations)
        gapLocation = shortGapLocations(locationIdx);
        firstFalseAfterTrue = trueSampleIndices(gapLocation) + 1;
        lastFalseBeforeNextTrue = trueSampleIndices(gapLocation + 1) - 1;
        if firstFalseAfterTrue <= lastFalseBeforeNextTrue
            filledMask(firstFalseAfterTrue:lastFalseBeforeNextTrue) = true;
        end
    end
end

function c = rgb(name)
%RGB Returns RGB triplet for a given color name string (case-insensitive).
% Usage:
%   c = rgb('SkyBlue');
%   c = rgb('LightGreen');
%   c = rgb('Lilac');
% Returns a 1x3 RGB vector in the range [0 1].

    if nargin == 0
        error('Please provide a color name.');
    end
    name = lower(string(name));

    % Define color dictionary
    cols = {
        % Blues
        {'navy', 'darkblue'},        [0 0 0.5];
        'blue',                      [0 0 1];
        'dodgerblue',                [0.12 0.56 1];
        'skyblue',                   [0.53 0.81 0.92];
        'lightblue',                 [0.68 0.85 0.9];
        'steelblue',                 [0.27 0.51 0.71];

        % Greens
        {'green','lime'},            [0 1 0];
        'forestgreen',               [0.13 0.55 0.13];
        'limegreen',                 [0.2 0.8 0.2];
        'lightgreen',                [0.56 0.93 0.56];
        'mediumseagreen',            [0.24 0.7 0.44];
        'springgreen',               [0 1 0.5];
        'charstreuse',                [0.5 1 0];

        % Reds
        'red',                       [1 0 0];
        'darkred',                   [0.55 0 0];
        'indianred',                 [0.8 0.36 0.36];
        'lightcoral',                [0.94 0.5 0.5];
        'salmon',                    [0.98 0.5 0.45];
        'tomato',                    [1 0.39 0.28];

        % Oranges
        'orange',                    [1 0.5 0];
        'darkorange',                [1 0.55 0];
        'coral',                     [1 0.5 0.31];
        'orangered',                 [1 0.27 0];

        % Yellows
        'yellow',                    [1 1 0];
        'gold',                      [1 0.84 0];
        'khaki',                     [0.94 0.9 0.55];
        'lightyellow',              [1 1 0.88];

        % Purples
        'purple',                    [0.5 0 0.5];
        'indigo',                    [0.29 0 0.51];
        'violet',                    [0.93 0.51 0.93];
        'mediumorchid',             [0.73 0.33 0.83];
        'plum',                      [0.87 0.63 0.87];

        % Pinks
        'pink',                      [1 0.75 0.8];
        'hotpink',                   [1 0.41 0.71];
        'deeppink',                  [1 0.08 0.58];
        'lightpink',                 [1 0.71 0.76];
        'palevioletred',             [0.86 0.44 0.58];

        % Browns
        'brown',                     [0.65 0.16 0.16];
        'sienna',                    [0.63 0.32 0.18];
        'saddlebrown',               [0.55 0.27 0.07];
        'chocolate',                 [0.82 0.41 0.12];
        'peru',                      [0.8 0.52 0.25];

        % Grays / Neutrals
        {'gray','grey'},             [0.5 0.5 0.5];
        'lightgray',                 [0.83 0.83 0.83];
        'darkgray',                  [0.66 0.66 0.66];
        'slategray',                 [0.44 0.5 0.56];
        'black',                     [0 0 0];
        'white',                     [1 1 1];

        % Turquoise / Teals
        {'teal','turquoise'},        [0 0.5 0.5];
        'mediumturquoise',           [0.28 0.82 0.8];
        'paleturquoise',             [0.69 0.93 0.93];

        % Magentas
        {'magenta','fuchsia'},       [1 0 1];
        'orchid',                    [0.85 0.44 0.84];
        'mediumvioletred',           [0.78 0.08 0.52];

        % Custom Aliases
        'lilac',                     [0.78 0.64 0.78];
    };

    % Search color
    c = [];
    for i = 1:size(cols, 1)
        keys = cols{i, 1};
        if iscell(keys)
            if any(strcmp(name, keys))
                c = cols{i,2};
                return;
            end
        elseif strcmp(name, keys)
            c = cols{i,2};
            return;
        end
    end

    error('Color "%s" not found.', name);
end

function DepthSort_meanWaveForms(ks_path,concat_f,session,settingsFile,elec,...
    guo,nchan,varargin)

% USAGE:
%     clu = DepthSort(fbasename,elec)
%
% DepthSort sort manually checked clusters based on the depth profile
%
% INPUT:
%     fbasename: char array
%     elec: a vector of electrode numbers
%
% optional:
%     AutoClustering(fbasename,elec,dim)
%     where dim is the number of channels in electrode group (if not
%     defined, will read the first line of the fet file
%
% Yuta Senzai 20170819

% 60 samples (2 ms) before and after spikestamp 
sbefore=60; 
safter=60;
sample_num = 1000;
totalch=384;
sr = 30000;

ntp = sbefore+safter;
tp = ((1:ntp)/sr)*1000; % ms

% Parameters
% Number recording sites
if ~isempty(varargin)
    dim = varargin{1};
    dim = dim(:);
    if any(double(int16(dim))~=dim)
        error('Number of dimensions must be an integer')
    end
    
    if size(dim,1) ~= length(elec) && length(dim) ~=1
        error('Number of dimensions must be a vector of the same length as electrode vecotr or a single value')
    end
    if length(dim) == 1
        dim = dim*ones(length(elec),1);
    end

else
    dim = zeros(length(elec),1);
end

elec = elec(:)';
if length(elec)>1
    for eIx=1:length(elec)
        % DepthSort_meanWaveForms(fbasename,elec(eIx),dim(eIx));
        DepthSort_meanWaveForms(ks_path,concat_f,settingsFile,elec(eIx),...
            guo,dim(eIx))
    end   
else
    % Load fet, clu and res
    fprintf('Sorting electrode %i of %s\n',elec,session)
    
    clu = readNPY(VisStimInputFile('spike_clusters'));
    spktimes = readNPY(VisStimInputFile('spike_times'));
    spktimes = spktimes+1;
    dat_info = dir(concat_f);
    if isempty(dat_info)
        error('Concatenated raw file not found: %s',concat_f);
    end
    bytesPerSample = 2; % int16
    dat_numel = floor(dat_info.bytes / bytesPerSample);
    fid_dat = fopen(concat_f,'r');
    if fid_dat < 0
        error('Could not open concatenated raw file: %s',concat_f);
    end
    cleanup_dat = onCleanup(@() fclose(fid_dat));

    labels_file = VisStimMatFile('New_Unit_Labels',[session,'.mat']);
    load(labels_file)

    if guo % good units only
        good_idx = strcmp(new_labels.KSLabel,'good');
        unit_list = new_labels.cluster_id(good_idx); % 0-indexing, corresponding to Phy
    else
        unit_list = unique(clu);
    end
    clu_num = length(unit_list);

    txt = fileread(settingsFile);
    channels_line = regexp(txt, '<CHANNELS[^>]*>', 'match', 'once');
    tokens = regexp(channels_line, '(CH\d+)="([^"]*)"', 'tokens');
    channel_names  = cellfun(@(x) x{1}, tokens, 'UniformOutput', false);
    chanmap = str2double(strrep(channel_names,'CH',''));
    % chanmap = 1:totalch;
    
    xp_line = regexp(txt, '<ELECTRODE_XPOS[^>]*>', 'match', 'once');
    tokens = regexp(xp_line, '(CH\d+)="(\d+)"', 'tokens');
    xpos = str2double(cellfun(@(x) x{2}, tokens, 'UniformOutput', false));

    yp_line = regexp(txt, '<ELECTRODE_YPOS[^>]*>', 'match', 'once');
    tokens = regexp(yp_line, '(CH\d+)="(\d+)"', 'tokens');
    ypos = str2double(cellfun(@(x) x{2}, tokens, 'UniformOutput', false));
     
    min_chan_idx = zeros(1,clu_num);
    ptp_chan_idx = zeros(1,clu_num);
    SpkAmpPrfl = zeros(totalch,clu_num);
    
    meanwavs = zeros(totalch,safter+sbefore,clu_num);
    for ii=1:clu_num % assuming that it is after manual curation
        % evenly sample 100 spikes from each cluster to calculate mean waveform
        
        cluspkIdx=find(clu==unit_list(ii));
        nSpk_clu = sum(clu==unit_list(ii));
        SampleStep = floor(nSpk_clu/sample_num);
        if SampleStep>0
            sampleIdx = 1:SampleStep:1+SampleStep*(sample_num-1);
            sampleList = cluspkIdx(sampleIdx);
        else
            sampleList = cluspkIdx;
        end
        
        if ~isempty(sampleList)
            wav = zeros(totalch,safter+sbefore,length(sampleList));
            for jj=1:length(sampleList)

                sample = sampleList(jj);
                st = spktimes(sample);

                start_idx = (double(st)-sbefore).*nchan + 1;
                end_idx = (double(st)+safter).*nchan;

                if start_idx < 1 || end_idx > dat_numel
                    fprintf(['Index exceeds the number of array elements: ii %d, ', ...
                        'jj %d\n'],ii,jj)
                    continue 
                end

                % w = dat.Data((double(spktimes(sampleList(jj)))-sbefore).*totalch+1:(double(spktimes(sampleList(jj)))+safter).*totalch);
                % w = dat.Data((double(st)-sbefore).*nchan+1:(double(st)+safter).*nchan);
                n_to_read = end_idx-start_idx+1;
                if fseek(fid_dat, (start_idx-1)*bytesPerSample, 'bof') ~= 0
                    fprintf('Could not seek raw file: ii %d, jj %d\n',ii,jj)
                    continue
                end
                w = fread(fid_dat, n_to_read, '*int16');
                if numel(w) ~= n_to_read
                    fprintf('Could not read full waveform: ii %d, jj %d\n',ii,jj)
                    continue
                end
              
                wvforms = reshape(w,nchan,[]);
                wvforms =  wvforms(chanmap+1,:);
                wvforms = wvforms-repmat(median(wvforms')',1,sbefore+safter);
                wav(:,:,jj)=wvforms;

                % fprintf('ii %d, jj %d\n',ii,jj)
            end
            mwav = squeeze(mean(wav,3));
            meanwavs(:,:,ii) = mwav;          
            m = mwav;
            
            ptp = max(m, [], 2) - min(m, [], 2);      
            [~, max_chan_idx] = max(ptp);    
            ptp_chan_idx(ii) = max_chan_idx; 


            ch_sets = {1:25;26:50;51:75;76:100;101:125;126:150;151:175;...
                176:200;201:225;226:250;251:275;276:300;301:325;326:350;...
                351:375;376:384};

            % for s = 1:length(ch_sets)
            %     figure
            %     sel = ch_sets{s};
            %     pos = 1;
            %     for ch_idx = sel  
            %         subplot(5,5,pos)
            %         plot(tp, m(ch_idx,:))
            % 
            %         uid = unit_list(ii);
            %         chan = chanmap(ch_idx);
            %         spt = sprintf('uid: %d, ch: %d', uid, chan);
            %         % spt = sprintf('uid: %d', uid);
            %         title(spt)      
            %         pos = pos+1;
            %     end
            % end
            
            % m_sort = m(:,sbefore); % 16 for old files;
            % [~,depth_cur] = min(m_sort);
            % min_chan_idx(ii) = depth_cur;            
            % 
            % SpkAmpPrfl(:,ii)=m_sort;
        end

        fprintf('%d out of %d units\n', ii, clu_num)
    end

    % Sort all the parameter based on depth
    % (clu, meanR, fractRogue) by Yuta 20170818
    
    % [sorted_depth,sortIdx] = sort(min_chan_idx);
    % 
    % newclu=clu;
    % 
    % newclu = updateclu(newclu,0,3000);
    % newclu = updateclu(newclu,1,3001);
    % for ii=1:clu_num
    %     newclu = updateclu(newclu,unit_list(sortIdx(ii)),ii+1+3000);
    %     % newclu(clu==ix(sortIdx(ii)))= ix(ii)+3000;
    % end
    % newclu = newclu-3000;

    % fid = fopen([fbasename '.clu.' num2str(elec)],'w'); 
    % fprintf(fid,'%i\n',[length(unique(newclu));newclu]);
    % fclose(fid);
    % save(['depthsort_parameter_' num2str(elec) '.mat'],'depth');
    
    %     figure;PlotColorMap(SpkAmpPrfl)
    % fig = figure;PlotColorMap(SpkAmpPrfl(:,sortIdx));
    % set(gca,'Ydir','reverse');
    % colorbar;
    % 
    % SaveFigPngPDFSvg(fig,[fbasename '_depthSort_waveAmp']);
    
    %% save meanWaveforms
    % meanWaveforms.data = meanwavs(:,:,sortIdx);
    meanWaveforms.data = meanwavs;
    meanWaveforms.samplenum = sample_num;
    meanWaveforms.sbefore = sbefore;
    meanWaveforms.safter = safter;
    meanWaveforms.unitIds = unit_list;
    meanWaveforms.chanmap = chanmap;
    meanWaveforms.min_chan_idx = min_chan_idx;
    meanWaveforms.ptp_chan_idx = ptp_chan_idx;
    meanWaveforms.timepts = tp;
    meanWaveforms.xpos = xpos;
    meanWaveforms.ypos = ypos;
    % meanWaveforms.info = 'dim1:channelID, dim2:time, dim3:cluID';
    % meanWaveforms.shankID = elec * ones(1,clu_num);
    % meanWaveforms.depth = sorted_depth;
    
   save_wfs = VisStimMatFile('SpikeWaveforms','mean_wfs',[session,'.mat']);
   EnsureParentDir(save_wfs);
   save(save_wfs,'meanWaveforms');   
end

end

function p = VisStimMatFile(varargin)
cfg = VisStimConfig();
p = fullfile(cfg.outputMatFilesFolder,varargin{:});
end

function p = VisStimSpreadsheetFile(fileName)
cfg = VisStimConfig();
switch fileName
    case 'Info_Grating.xlsx'
        p = cfg.gratingSpreadsheetFile;
    case 'Info_RFmapping.xlsx'
        p = cfg.rfSpreadsheetFile;
    otherwise
        error('Set a literal spreadsheet file path for %s in VisStimConfig().',fileName);
end
end

function p = VisStimStimFile(stimKey)
cfg = VisStimConfig();
stimKey = VisStimText(stimKey);
if strcmpi(stimKey,'grating')
    p = cfg.gratingStimFile;
elseif strcmpi(stimKey,'rfmapping')
    p = cfg.rfStimFile;
elseif VisStimIsAbsolute(stimKey)
    p = stimKey;
else
    error('Set cfg.%sStimFile as a literal file path in VisStimConfig().',stimKey);
end
end

function p = VisStimSettingsFile(varargin)
cfg = VisStimConfig();
p = cfg.settingsFile;
end

function nodeId = VisStimOneBoxNodeId(settingsFile)
settingsFile = VisStimText(settingsFile);
if exist(settingsFile,'file') ~= 2
    error('settings.xml not found: %s',settingsFile);
end

txt = fileread(settingsFile);
processorTags = regexp(txt, '<PROCESSOR\b[^>]*>', 'match');
oneBoxTags = processorTags(contains(processorTags, 'name="OneBox"'));
if isempty(oneBoxTags)
    error('OneBox PROCESSOR node not found in settings.xml: %s',settingsFile);
end

tokens = regexp(oneBoxTags{1}, 'nodeId="([^"]+)"', 'tokens', 'once');
if isempty(tokens)
    error('OneBox nodeId not found in settings.xml: %s',settingsFile);
end
nodeId = string(tokens{1});
end

function p = VisStimProbePath(probeLetter,pathType)
cfg = VisStimConfig();
switch pathType
    case 'kilosort'
        p = fileparts(cfg.spikeClustersFile);
    case 'concat_raw'
        p = cfg.probeConcatRawFile;
    otherwise
        error('Unknown VisStimProbePath type: %s',pathType);
end
end

function p = VisStimAdcConcatFile(ksPath)
cfg = VisStimConfig();
p = cfg.adcConcatRawFile;
end

function p = VisStimInputFile(fileKey)
cfg = VisStimConfig();
switch fileKey
    case 'cluster_KSLabel'
        p = cfg.clusterKSLabelFile;
    case 'cluster_group'
        p = cfg.clusterGroupFile;
    case 'spike_clusters'
        p = cfg.spikeClustersFile;
    case 'spike_times'
        p = cfg.spikeTimesFile;
    case 'adc_spike_times'
        p = cfg.adcSpikeTimesFile;
    case 'probe_concat_raw'
        p = cfg.probeConcatRawFile;
    case 'adc_concat_raw'
        p = cfg.adcConcatRawFile;
    otherwise
        error('Unknown input file key: %s',fileKey);
end
end

function p = VisStimRawFile(sessionId,fileKey)
cfg = VisStimConfig();
sessionId = VisStimFileStem(sessionId);

if strcmp(sessionId,cfg.sessionFolder)
    switch fileKey
        case 'settings'
            p = cfg.settingsFile;
        case 'adc_continuous'
            p = cfg.adcContinuousFile;
        case 'adc_continuous_sample_numbers'
            p = cfg.adcContinuousSampleNumbersFile;
        case 'adc_ttl_timestamps'
            p = cfg.adcTtlTimestampsFile;
        case 'adc_ttl_sample_numbers'
            p = cfg.adcTtlSampleNumbersFile;
        case 'probe_continuous'
            p = cfg.probeContinuousFile;
        case 'probe_continuous_sample_numbers'
            p = cfg.probeContinuousSampleNumbersFile;
        case 'probe_ttl_timestamps'
            p = cfg.probeTtlTimestampsFile;
        case 'probe_ttl_sample_numbers'
            p = cfg.probeTtlSampleNumbersFile;
        otherwise
            error('Unknown raw file key: %s',fileKey);
    end
    return
end

rows = cfg.precedingSessionFiles;
idx = strcmp(rows(:,1),sessionId) & strcmp(rows(:,2),fileKey);
if ~any(idx)
    error('Set the literal file path for %s / %s in cfg.precedingSessionFiles.',sessionId,fileKey);
end
p = rows{find(idx,1),3};
end

function sessionId = VisStimPrecedingSession(currentSession,precedingSuffix)
currentSession = VisStimFileStem(currentSession);
precedingSuffix = VisStimText(precedingSuffix);
if numel(currentSession) >= 8
    sessionId = [currentSession(1:8),precedingSuffix];
else
    sessionId = precedingSuffix;
end
end

function stem = VisStimFileStem(pathValue)
s = VisStimText(pathValue);
s = strrep(s,'\','/');
while ~isempty(s) && strcmp(s(end),'/')
    s(end) = [];
end
[~,stem] = fileparts(s);
if isempty(stem)
    stem = s;
end
end

function s = VisStimText(value)
if iscell(value)
    if isempty(value)
        s = '';
        return
    end
    value = value{1};
end

if isstring(value)
    if ismissing(value)
        s = '';
        return
    end
    value = char(value);
end

if isnumeric(value)
    if isempty(value) || (isscalar(value) && isnan(value))
        s = '';
    else
        s = num2str(value);
    end
    return
end

s = strtrim(char(value));
end

function tf = VisStimIsAbsolute(pathValue)
s = VisStimText(pathValue);
tf = ~isempty(s) && (strcmp(s(1),'/') || ~isempty(regexp(s,'^[A-Za-z]:[\\/]', 'once')));
end

function EnsureParentDir(filePath)
[parentDir,~,~] = fileparts(filePath);
EnsureDir(parentDir);
end

function EnsureDir(dirPath)
if ~isempty(dirPath) && ~isfolder(dirPath)
    mkdir(dirPath);
end
end

function data = readNPY(filename)
fid = fopen(filename,'r');
if fid < 0
    error('readNPY:FileNotFound','Could not open NPY file: %s',filename);
end
cleanup = onCleanup(@() fclose(fid));

magic = fread(fid,6,'*uint8')';
if numel(magic) ~= 6 || ~isequal(char(magic),char([147,'NUMPY']))
    error('readNPY:InvalidFile','Invalid NPY file: %s',filename);
end

major = fread(fid,1,'uint8');
fread(fid,1,'uint8');
if major == 1
    headerLen = fread(fid,1,'uint16',0,'ieee-le');
else
    headerLen = fread(fid,1,'uint32',0,'ieee-le');
end
header = char(fread(fid,headerLen,'*char')');

descrTok = regexp(header, '''descr''\s*:\s*''([^'']+)''', 'tokens', 'once');
fortranTok = regexp(header, '''fortran_order''\s*:\s*(True|False)', 'tokens', 'once');
shapeTok = regexp(header, '''shape''\s*:\s*\(([^\)]*)\)', 'tokens', 'once');
if isempty(descrTok) || isempty(fortranTok) || isempty(shapeTok)
    error('readNPY:InvalidHeader','Could not parse NPY header in %s',filename);
end

descr = descrTok{1};
fortranOrder = strcmp(fortranTok{1},'True');
shapeText = strtrim(shapeTok{1});
shapeParts = regexp(shapeText, ',', 'split');
shape = [];
for i = 1:numel(shapeParts)
    val = strtrim(shapeParts{i});
    if ~isempty(val)
        shape(end+1) = str2double(val); %#ok<AGROW>
    end
end
if isempty(shape)
    shape = 1;
end

[precision, machinefmt] = VisStimNpyPrecision(descr);
data = fread(fid, prod(shape), ['*',precision], 0, machinefmt);
if numel(shape) > 1
    if fortranOrder
        data = reshape(data, shape);
    else
        data = reshape(data, fliplr(shape));
        data = permute(data, numel(shape):-1:1);
    end
end
end

function [precision, machinefmt] = VisStimNpyPrecision(descr)
byteOrder = descr(1);
typeCode = descr(2);
byteCount = str2double(descr(3:end));

if byteOrder == '<'
    machinefmt = 'ieee-le';
elseif byteOrder == '>'
    machinefmt = 'ieee-be';
else
    machinefmt = 'n';
end

switch typeCode
    case 'i'
        precision = sprintf('int%d', byteCount*8);
    case 'u'
        precision = sprintf('uint%d', byteCount*8);
    case 'f'
        if byteCount == 4
            precision = 'single';
        elseif byteCount == 8
            precision = 'double';
        else
            error('readNPY:UnsupportedType','Unsupported NPY float type: %s',descr);
        end
    case 'b'
        precision = 'logical';
    otherwise
        error('readNPY:UnsupportedType','Unsupported NPY type: %s',descr);
end
end

function data = LoadBinary(filename,varargin)
p = inputParser;
addRequired(p,'filename',@(x) ischar(x) || isstring(x));
addParameter(p,'nChannels',1,@isnumeric);
addParameter(p,'channels',1,@isnumeric);
addParameter(p,'precision','int16',@(x) ischar(x) || isstring(x));
addParameter(p,'frequency',[],@isnumeric);
addParameter(p,'start',0,@isnumeric);
addParameter(p,'duration',inf,@isnumeric);
parse(p,filename,varargin{:});

filename = char(filename);
nChannels = p.Results.nChannels;
channels = p.Results.channels;
precision = char(p.Results.precision);
frequency = p.Results.frequency;
startTime = p.Results.start;
duration = p.Results.duration;

info = dir(filename);
if isempty(info)
    error('LoadBinary:FileNotFound','Could not open binary file: %s',filename);
end

bytesPerSample = VisStimBytesPerSample(precision);
totalSamples = floor(info.bytes / (nChannels * bytesPerSample));
if isempty(frequency)
    firstSample = max(1, floor(startTime)+1);
    if isinf(duration)
        lastSample = totalSamples;
    else
        lastSample = min(totalSamples, firstSample + floor(duration) - 1);
    end
else
    firstSample = max(1, floor(startTime * frequency) + 1);
    if isinf(duration)
        lastSample = totalSamples;
    else
        lastSample = min(totalSamples, firstSample + floor(duration * frequency) - 1);
    end
end

sampleCount = max(0, lastSample - firstSample + 1);
if sampleCount == 0
    data = [];
    return
end

mm = memmapfile(filename,'Format',precision);
idx0 = (firstSample-1) * nChannels;
data = zeros(sampleCount,numel(channels),precision);
for c = 1:numel(channels)
    channel = channels(c);
    idx = idx0 + channel:nChannels:idx0 + (sampleCount-1) * nChannels + channel;
    data(:,c) = mm.Data(idx);
end
if isscalar(channels)
    data = data(:,1);
end
end

function n = VisStimBytesPerSample(precision)
switch char(precision)
    case {'int8','uint8','char','uchar','schar'}
        n = 1;
    case {'int16','uint16','short','ushort'}
        n = 2;
    case {'int32','uint32','single','float','int','uint'}
        n = 4;
    case {'int64','uint64','double'}
        n = 8;
    otherwise
        error('LoadBinary:UnsupportedPrecision','Unsupported binary precision: %s',precision);
end
end

function [sync,si] = Sync(spikeTimes,eventTimes,varargin)
p = inputParser;
addRequired(p,'spikeTimes',@isnumeric);
addRequired(p,'eventTimes',@isnumeric);
addParameter(p,'durations',[-1 1],@isnumeric);
parse(p,spikeTimes,eventTimes,varargin{:});

spikeTimes = spikeTimes(:);
eventTimes = eventTimes(:);
win = p.Results.durations;
sync = [];
si = [];

for i = 1:numel(eventTimes)
    rel = spikeTimes - eventTimes(i);
    keep = rel >= win(1) & rel <= win(2);
    sync = [sync; rel(keep)]; %#ok<AGROW>
    si = [si; i * ones(sum(keep),1)]; %#ok<AGROW>
end
end
