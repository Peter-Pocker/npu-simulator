$pdf_mode = 1;
$xelatex = "xelatex -synctex=1 -shell-escape -interaction=nonstopmode %O %S";
$pdflatex = "xelatex -synctex=1 -shell-escape -interaction=nonstopmode %O %S";
$biber = "biber %O %S";
$preview_mode = 1;
$cleanup_mode = 2;

# 输出到 build/，这样 make pdf 最后的 cp build/main.pdf main.pdf 会复制到正确的文件
$out_dir = 'build';

add_cus_dep('glo', 'gls', 0, 'run_makeglossaries');
add_cus_dep('acn', 'acr', 0, 'run_makeglossaries');

sub run_makeglossaries {
  my $base = $_[0];
  if ($out_dir && $base =~ /^\Q$out_dir\E\/(.*)/) {
    my $name = $1;
    my $q = $silent ? "-q" : "";
    system "cd \"$out_dir\" && makeglossaries $q \"$name\"";
  } else {
    my $q = $silent ? "-q" : "";
    system "makeglossaries $q \"$base\"";
  }
}

push @generated_exts, 'glo', 'gls', 'glg';
push @generated_exts, 'acn', 'acr', 'alg';
push @generated_exts, 'synctex.gz';

$clean_ext = 'acn acr alg aux bbl bcf blg brf fdb_latexmk glg glo gls idx ilg ind ist lof log lot out run.xml toc toe dvi slg slo sls xdy listing';

